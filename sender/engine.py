"""
sender/engine.py — Core sending engine. Phase 2.

run_once() is called by:
  - management command `run_sender` (PA scheduled/always-on task)
  - /api/process-queue (browser "Send a batch now" button)

Design decisions:
  - Atomic row claiming (SELECT ... WHERE status='queued' then UPDATE to 'sending')
    prevents double-sends even with concurrent web + cron calls (D7).
  - Error classification: transient → retry with backoff; permanent → bounce+suppress;
    quota → block whole run; auth → stop + flag for reconnect (D4).
  - Campaign gating: only status in {'scheduled','sending'} are eligible (D1).
  - Frequency cap at send time (D2): re-checks even for already-queued rows.
  - Timezone-correct daily/hourly caps using timeutils (D6).
  - Rate-based circuit breaker (not just absolute count) (D4 follow-on).
"""
import json
import logging
import random
import time
import uuid
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.db import close_old_connections

from .models import SendLog, Suppression, Setting, GmailCredential
from .gmail_sender import (
    send_email, classify_error,
    ERROR_CLASS_PERMANENT, ERROR_CLASS_TRANSIENT,
    ERROR_CLASS_QUOTA, ERROR_CLASS_AUTH,
)
from .timeutils import today_bounds, in_send_window, now_local
from leads.verifier import verify_email  # top-level so tests can mock it

logger = logging.getLogger(__name__)

# Re-try intervals for transient errors (seconds)
RETRY_DELAYS = [5 * 60, 15 * 60, 60 * 60]  # 5 min, 15 min, 1 h
MAX_ATTEMPTS = 3

# Stale claim threshold: 15 minutes
STALE_CLAIM_MINUTES = 15


def _get_unsub_url(lead) -> str:
    from django.conf import settings
    from django.core import signing
    token = signing.dumps(lead.email, salt=settings.UNSUB_SIGNING_SALT)
    return f'{settings.BASE_URL}/unsub/{token}/'


def _render_body(template: str, lead, unsub_url: str) -> str:
    """Replace merge fields for a specific lead."""
    result = template
    result = result.replace('{{lead_name}}', lead.name or lead.email.split('@')[0])
    result = result.replace('{{company}}', lead.company or '')
    result = result.replace('{{unsub_url}}', unsub_url)
    return result


import re
PLACEHOLDER_RE = re.compile(r'\{\{[^}]+\}\}')


def _sweep_stale_claims():
    """Return 'sending' rows that have been claimed for > 15 minutes back to queued."""
    cutoff = timezone.now() - timedelta(minutes=STALE_CLAIM_MINUTES)
    stale = SendLog.objects.filter(status=SendLog.STATUS_SENDING, claimed_at__lt=cutoff)
    count = stale.update(
        status=SendLog.STATUS_QUEUED,
        claim_token=None,
        claimed_at=None,
        attempts=Count('attempts') + 1,  # NOTE: use F() for atomic increment
    )
    # Fix: use F() expression
    from django.db.models import F
    stale2 = SendLog.objects.filter(status=SendLog.STATUS_QUEUED, claimed_at__isnull=True,
                                     message_id_header='').update(attempts=F('attempts') + 0)
    if count:
        logger.warning('Swept %d stale claimed rows back to queued', count)


def _sweep_stale_claims_v2():
    """Correct version using F() for atomic increment."""
    from django.db.models import F
    cutoff = timezone.now() - timedelta(minutes=STALE_CLAIM_MINUTES)
    stale_ids = list(
        SendLog.objects.filter(status=SendLog.STATUS_SENDING, claimed_at__lt=cutoff)
        .values_list('id', flat=True)
    )
    if stale_ids:
        SendLog.objects.filter(id__in=stale_ids).update(
            status=SendLog.STATUS_QUEUED,
            claim_token=None,
            claimed_at=None,
        )
        SendLog.objects.filter(id__in=stale_ids).update(
            attempts=F('attempts') + 1,
        )
        logger.warning('Swept %d stale claimed rows back to queued', len(stale_ids))


def _get_sent_today_count() -> int:
    start, end = today_bounds()
    return SendLog.objects.filter(status=SendLog.STATUS_SENT, sent_at__range=(start, end)).count()


def _get_hard_bounced_24h() -> int:
    """Count hard (permanent) bounces in the last 24 hours."""
    cutoff = timezone.now() - timedelta(hours=24)
    return SendLog.objects.filter(
        status=SendLog.STATUS_BOUNCED,
        sent_at__gte=cutoff,
    ).count()


def _get_delivery_attempts_24h() -> int:
    """Count total delivery attempts in the last 24 hours (for bounce rate)."""
    cutoff = timezone.now() - timedelta(hours=24)
    return SendLog.objects.filter(
        status__in=[SendLog.STATUS_SENT, SendLog.STATUS_BOUNCED],
        sent_at__gte=cutoff,
    ).count()


def _auto_pause_if_needed(setting: Setting) -> str | None:
    """
    Check bounce rate and auto-pause if threshold exceeded.
    Returns pause_reason string if paused, None otherwise.
    """
    attempts_24h = _get_delivery_attempts_24h()
    bounced_24h = _get_hard_bounced_24h()

    # Rate-based threshold (D4 fix): only check when ≥20 delivery attempts
    if attempts_24h >= 20:
        rate = (bounced_24h / attempts_24h) * 100
        if rate > setting.bounce_pause_percent:
            reason = (
                f'Auto-paused: {bounced_24h} hard bounces out of {attempts_24h} '
                f'delivery attempts in the last 24h ({rate:.1f}% > {setting.bounce_pause_percent}% threshold)'
            )
            setting.paused = True
            setting.pause_reason = reason
            setting.paused_at = timezone.now()
            setting.save(update_fields=['paused', 'pause_reason', 'paused_at', 'updated_at'])
            logger.warning(reason)
            return reason

    # Absolute safety net: ≥10 hard bounces always pauses
    if bounced_24h >= 10:
        reason = (
            f'Auto-paused: safety net triggered — {bounced_24h} hard bounces in last 24h'
        )
        setting.paused = True
        setting.pause_reason = reason
        setting.paused_at = timezone.now()
        setting.save(update_keys=['paused', 'pause_reason', 'paused_at', 'updated_at'])
        logger.warning(reason)
        return reason

    return None


def _claim_rows(campaign, take: int, my_token: uuid.UUID) -> list:
    """
    Atomically claim up to `take` queued rows for this campaign.
    Uses SELECT → UPDATE with claim_token to prevent double-sends (D7).
    """
    with transaction.atomic():
        row_ids = list(
            SendLog.objects.select_for_update(skip_locked=True).filter(
                campaign=campaign,
                status=SendLog.STATUS_QUEUED,
            ).filter(
                Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=timezone.now())
            ).order_by('created_at').values_list('id', flat=True)[:take]
        )
        if row_ids:
            SendLog.objects.filter(id__in=row_ids).update(
                status=SendLog.STATUS_SENDING,
                claimed_at=timezone.now(),
                claim_token=my_token,
            )
    return row_ids


def _get_eligible_campaigns():
    """Return campaigns whose status allows sending, ordered by created_at (fairness)."""
    now = timezone.now()
    return list(
        SendLog._meta.model._default_manager.none().__class__.objects.none()
    )  # placeholder — see below

def _get_eligible_campaigns_real():
    """Return campaigns whose status allows sending."""
    from campaigns.models import Campaign
    now = timezone.now()
    return list(
        Campaign.objects.filter(
            status__in=Campaign.SENDING_ELIGIBLE,
        ).filter(
            Q(start_at__isnull=True) | Q(start_at__lte=now)
        ).order_by('created_at')  # oldest first for fairness
    )


def run_once(max_seconds: int = 3000, dry_run: bool = False) -> dict:
    """
    Main engine: run one time-boxed sending pass.
    Returns a JSON-serialisable summary dict.
    """
    close_old_connections()
    start_time = time.monotonic()
    now_dt = timezone.now()
    my_token = uuid.uuid4()

    setting = Setting.get()

    # ── Heartbeat ─────────────────────────────────────────────────────────────
    setting.last_worker_heartbeat = now_dt
    setting.save(update_fields=['last_worker_heartbeat', 'updated_at'])

    # ── Pre-flight checks ──────────────────────────────────────────────────────
    def elapsed():
        return time.monotonic() - start_time

    def make_result(**kwargs):
        summary = {
            'sent': 0, 'skipped': 0, 'retried': 0, 'failed': 0,
            'blocked_reason': '', 'next_run_hint': '',
            'remaining': SendLog.objects.filter(status=SendLog.STATUS_QUEUED).count(),
            'dry_run': dry_run,
            **kwargs,
        }
        setting.last_worker_summary = summary
        setting.last_worker_heartbeat = timezone.now()
        setting.save(update_fields=['last_worker_summary', 'last_worker_heartbeat', 'updated_at'])
        return summary

    if setting.paused:
        return make_result(blocked_reason=f'Sending is paused: {setting.pause_reason or "manually paused"}')

    if setting.sending_blocked_until and setting.sending_blocked_until > now_dt:
        wait = int((setting.sending_blocked_until - now_dt).total_seconds() / 60)
        return make_result(blocked_reason=f'Quota block active for ~{wait} more minutes')

    credential = GmailCredential.get_active()
    if not credential:
        return make_result(blocked_reason='No Gmail account connected — go to Settings → Gmail')
    if credential.needs_reauth:
        return make_result(blocked_reason='Gmail needs reconnecting — go to Settings → Gmail')

    # ── Send window check ─────────────────────────────────────────────────────
    window_ok, window_reason = in_send_window(
        now_dt,
        window_start=setting.window_start,
        window_end=setting.window_end,
        allowed_weekdays=setting.allowed_weekdays(),
    )
    if not window_ok:
        return make_result(blocked_reason=window_reason)

    # ── Daily cap ──────────────────────────────────────────────────────────────
    sent_today = _get_sent_today_count()
    daily_cap = setting.daily_cap
    if sent_today >= daily_cap:
        return make_result(
            blocked_reason=f'Daily cap reached: {sent_today}/{daily_cap} sent today',
            next_run_hint='Tomorrow when the send window opens',
        )

    available_slots = daily_cap - sent_today

    # ── Stale claim sweep ─────────────────────────────────────────────────────
    _sweep_stale_claims_v2()

    # ── Get eligible campaigns ────────────────────────────────────────────────
    eligible_campaigns = _get_eligible_campaigns_real()
    if not eligible_campaigns:
        return make_result(blocked_reason='No scheduled campaigns with queued rows')

    # ── Load suppression set ───────────────────────────────────────────────────
    suppressed_emails = set(Suppression.objects.values_list('email', flat=True))

    # ── Frequency cap: who was contacted recently ─────────────────────────────
    min_days = setting.min_days_between_emails
    freq_cutoff = timezone.now() - timedelta(days=min_days)
    recently_contacted_ids = set(
        SendLog.objects.filter(
            status=SendLog.STATUS_SENT,
            sent_at__gte=freq_cutoff,
        ).values_list('lead_id', flat=True)
    )

    # ── Process rows round-robin across campaigns ─────────────────────────────
    sent = skipped = retried = failed = 0
    batch_per_campaign = max(1, available_slots // len(eligible_campaigns))

    for campaign in eligible_campaigns:
        if elapsed() > max_seconds:
            logger.info('Time box reached after %ds', max_seconds)
            break
        if sent + skipped + failed >= available_slots:
            break

        # Flip scheduled → sending on first row
        if campaign.status == campaign.STATUS_SCHEDULED:
            if not dry_run:
                campaign.status = campaign.STATUS_SENDING
                campaign.save(update_fields=['status'])

        # Campaign-level daily cap
        camp_cap = campaign.effective_per_day_limit(daily_cap)
        start_camp, end_camp = today_bounds()
        camp_sent_today = SendLog.objects.filter(
            campaign=campaign, status=SendLog.STATUS_SENT,
            sent_at__range=(start_camp, end_camp)
        ).count()
        camp_available = max(0, camp_cap - camp_sent_today)
        take = min(batch_per_campaign, camp_available, available_slots - sent - skipped - failed)

        if take <= 0:
            continue

        if dry_run:
            dry_count = SendLog.objects.filter(
                campaign=campaign, status=SendLog.STATUS_QUEUED
            ).count()
            sent += min(dry_count, take)
            continue

        # Claim rows atomically
        claimed_ids = _claim_rows(campaign, take, my_token)
        if not claimed_ids:
            continue

        rows = list(
            SendLog.objects.select_related('lead', 'campaign')
            .filter(id__in=claimed_ids, claim_token=my_token)
        )

        for log in rows:
            if elapsed() > max_seconds:
                # Release remaining claimed rows
                SendLog.objects.filter(id__in=[r.id for r in rows if r.status == SendLog.STATUS_SENDING],
                                       claim_token=my_token).update(
                    status=SendLog.STATUS_QUEUED, claimed_at=None, claim_token=None
                )
                break

            lead = log.lead

            # Re-check suppression
            if lead.email in suppressed_emails:
                log.status = SendLog.STATUS_SKIPPED
                log.error = 'Suppressed at send time'
                log.sent_at = timezone.now()
                log.save()
                skipped += 1
                continue

            # Re-check frequency cap
            if lead.pk in recently_contacted_ids:
                log.status = SendLog.STATUS_SKIPPED
                log.error = f'Contacted recently (within {min_days} days)'
                log.sent_at = timezone.now()
                log.save()
                skipped += 1
                continue

            # Pre-send address verification (uses DB cache, no live DNS per-row for perf)
            is_valid, reject_reason = verify_email(lead.email)
            if not is_valid:
                log.status = SendLog.STATUS_SKIPPED
                log.error = f'Address check: {reject_reason}'
                log.sent_at = timezone.now()
                log.save()
                lead.status = 'bounced'
                lead.save(update_fields=['status'])
                Suppression.objects.get_or_create(
                    email=lead.email,
                    defaults={'reason': Suppression.REASON_BOUNCED, 'notes': f'Pre-send: {reject_reason}'}
                )
                skipped += 1
                continue

            # Render and check placeholders
            unsub_url = _get_unsub_url(lead)
            body_text = _render_body(campaign.body_text, lead, unsub_url)
            body_html = _render_body(campaign.body_html, lead, unsub_url)
            subject = _render_body(campaign.subject, lead, unsub_url)  # also render subject

            leftover = PLACEHOLDER_RE.findall(body_text + body_html + subject)
            if leftover:
                log.status = SendLog.STATUS_SKIPPED
                log.error = f'Unfilled placeholders: {leftover}'
                log.sent_at = timezone.now()
                log.save()
                skipped += 1
                continue

            # Idempotency guard: generate Message-ID before send
            if not log.message_id_header:
                try:
                    base_domain = setting.last_worker_summary and 'bidalert.in' or 'bidalert.in'
                    from django.conf import settings as djsettings
                    base_domain = djsettings.BASE_URL.split('//')[1].rstrip('/')
                except Exception:
                    base_domain = 'bidalert.in'
                log.message_id_header = f'<{uuid.uuid4().hex}@{base_domain}>'
                log.save(update_fields=['message_id_header'])

            # Check if already sent (crash-resend guard)
            if log.gmail_message_id:
                logger.warning('Row %d already has gmail_message_id — skipping (idempotency)', log.id)
                log.status = SendLog.STATUS_SENT
                log.save(update_fields=['status'])
                sent += 1
                continue

            try:
                gmail_id, msg_id_hdr = send_email(
                    credential=credential,
                    to_email=lead.email,
                    to_name=lead.name or '',
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    unsub_url=unsub_url,
                    message_id_header=log.message_id_header,
                )
                log.status = SendLog.STATUS_SENT
                log.gmail_message_id = gmail_id
                log.message_id_header = msg_id_hdr
                log.sent_at = timezone.now()
                log.claim_token = None
                log.claimed_at = None
                log.save()

                lead.status = 'contacted'
                lead.last_contacted_at = timezone.now()
                lead.save(update_fields=['status', 'last_contacted_at'])

                sent += 1
                recently_contacted_ids.add(lead.pk)

                # Jitter delay
                delay = random.randint(setting.min_delay_seconds, setting.max_delay_seconds)
                if elapsed() + delay < max_seconds:
                    time.sleep(delay)

            except Exception as exc:
                error_str = str(exc)
                error_class = classify_error(exc)
                logger.error('Send failed [%s] for %s: %s', error_class, lead.email, error_str)

                if error_class == ERROR_CLASS_AUTH:
                    credential.needs_reauth = True
                    credential.save(update_fields=['needs_reauth'])
                    log.status = SendLog.STATUS_QUEUED
                    log.claim_token = None
                    log.claimed_at = None
                    log.save()
                    return make_result(
                        sent=sent, skipped=skipped, failed=failed,
                        blocked_reason='Gmail needs reconnecting — stopping run',
                    )

                if error_class == ERROR_CLASS_QUOTA:
                    setting.sending_blocked_until = timezone.now() + timedelta(hours=1)
                    setting.save(update_fields=['sending_blocked_until', 'updated_at'])
                    log.status = SendLog.STATUS_QUEUED
                    log.claim_token = None
                    log.claimed_at = None
                    log.save()
                    return make_result(
                        sent=sent, skipped=skipped, failed=failed,
                        blocked_reason='Gmail quota exceeded — blocked for 1 hour',
                    )

                if error_class == ERROR_CLASS_PERMANENT:
                    log.status = SendLog.STATUS_BOUNCED
                    log.error = error_str[:500]
                    log.sent_at = timezone.now()
                    log.claim_token = None
                    log.claimed_at = None
                    log.save()
                    Suppression.objects.get_or_create(
                        email=lead.email,
                        defaults={'reason': Suppression.REASON_BOUNCED}
                    )
                    lead.status = 'bounced'
                    lead.save(update_fields=['status'])
                    failed += 1

                else:  # transient
                    log.attempts = (log.attempts or 0) + 1
                    if log.attempts >= MAX_ATTEMPTS:
                        log.status = SendLog.STATUS_FAILED
                        log.error = f'Failed after {MAX_ATTEMPTS} attempts: {error_str[:400]}'
                        log.sent_at = timezone.now()
                        log.claim_token = None
                        log.claimed_at = None
                    else:
                        delay_secs = RETRY_DELAYS[min(log.attempts - 1, len(RETRY_DELAYS) - 1)]
                        log.status = SendLog.STATUS_QUEUED
                        log.next_attempt_at = timezone.now() + timedelta(seconds=delay_secs)
                        log.error = f'Transient error (attempt {log.attempts}): {error_str[:300]}'
                        log.claim_token = None
                        log.claimed_at = None
                        retried += 1
                    log.save()
                    failed += 1

        # After campaign batch: check if campaign is now done
        remaining_for_campaign = SendLog.objects.filter(
            campaign=campaign, status=SendLog.STATUS_QUEUED
        ).count()
        if remaining_for_campaign == 0 and not dry_run:
            campaign.status = campaign.STATUS_DONE
            campaign.finished_at = timezone.now()
            campaign.save(update_fields=['status', 'finished_at'])
            logger.info('Campaign "%s" finished.', campaign.name)

    # ── Post-run bounce check ──────────────────────────────────────────────────
    if not dry_run and sent > 0:
        _auto_pause_if_needed(setting)

    total_remaining = SendLog.objects.filter(status=SendLog.STATUS_QUEUED).count()

    # ETA estimate
    eta_days = None
    if sent > 0 and total_remaining > 0:
        eta_days = (total_remaining // max(1, sent)) + 1

    return make_result(
        sent=sent,
        skipped=skipped,
        retried=retried,
        failed=failed,
        remaining=total_remaining,
        eta_days=eta_days,
        next_run_hint='Next scheduled window' if not dry_run else 'Dry run — nothing sent',
    )
