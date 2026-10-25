"""
sender/scheduler_views.py — Dedicated Scheduler & Automation Hub.
Manages sending window, daily caps, rate limits, worker heartbeat,
and controls upcoming scheduled campaigns.
"""
import json
import logging
from datetime import datetime, time as dt_time, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_time
from django.views.decorators.http import require_POST

from accounts.rbac import ops_required
from campaigns.models import Campaign
from leads.models import Lead
from sender.models import Setting, SendLog, GmailCredential
from sender.engine import run_once
from sender.timeutils import in_send_window, next_window_open, now_local, today_bounds, get_app_tz

logger = logging.getLogger(__name__)


@ops_required
def scheduler_dashboard(request):
    """
    Main Scheduler Dashboard view.
    Displays master engine switch, delivery window, rate limits,
    upcoming scheduled campaigns, and live queue status.
    """
    setting = Setting.get()
    now_dt = timezone.now()
    local_now = now_local()
    tz = get_app_tz()

    # Calculate send window status
    allowed_days = setting.allowed_weekdays()
    window_ok, window_reason = in_send_window(
        now_dt,
        window_start=setting.window_start,
        window_end=setting.window_end,
        allowed_weekdays=allowed_days,
    )
    next_open_dt = next_window_open(setting.window_start, allowed_days) if not window_ok else None

    # Calculate today's quota and counts
    start_today, end_today = today_bounds()
    sent_today = SendLog.objects.filter(
        status=SendLog.STATUS_SENT,
        sent_at__range=(start_today, end_today),
    ).count()

    daily_cap = setting.daily_cap or 50
    cap_pct = min(100, int((sent_today / daily_cap) * 100)) if daily_cap else 0
    remaining_today = max(0, daily_cap - sent_today)

    # Queue stats
    total_queued = SendLog.objects.filter(status=SendLog.STATUS_QUEUED).count()

    # Active & Scheduled campaigns
    scheduled_campaigns = list(
        Campaign.objects.filter(
            status__in=[Campaign.STATUS_SCHEDULED, Campaign.STATUS_SENDING, Campaign.STATUS_PAUSED]
        ).order_by('start_at', 'created_at')
    )

    # Attach live metrics to each campaign
    for camp in scheduled_campaigns:
        sent_c = camp.sent_count
        queued_c = camp.queued_count
        total = camp.planned_total or (sent_c + queued_c)
        camp.progress_pct = int((sent_c / total) * 100) if total > 0 else 0
        camp.is_due = bool(camp.start_at and camp.start_at <= now_dt)
        if camp.start_at:
            camp.start_at_local = camp.start_at.astimezone(tz)
        else:
            camp.start_at_local = None

    # Worker heartbeat status
    last_heartbeat = setting.last_worker_heartbeat
    is_worker_alive = False
    heartbeat_ago_str = 'Never'
    if last_heartbeat:
        diff = (now_dt - last_heartbeat).total_seconds()
        is_worker_alive = diff < 600  # alive if checked in within 10 minutes
        if diff < 60:
            heartbeat_ago_str = f'{int(diff)}s ago'
        elif diff < 3600:
            heartbeat_ago_str = f'{int(diff / 60)}m ago'
        else:
            heartbeat_ago_str = f'{int(diff / 3600)}h ago'

    # Gmail credential status
    gmail_cred = GmailCredential.get_active()

    # Days of week options
    days_data = [
        {'id': 0, 'name': 'Mon', 'checked': 0 in allowed_days},
        {'id': 1, 'name': 'Tue', 'checked': 1 in allowed_days},
        {'id': 2, 'name': 'Wed', 'checked': 2 in allowed_days},
        {'id': 3, 'name': 'Thu', 'checked': 3 in allowed_days},
        {'id': 4, 'name': 'Fri', 'checked': 4 in allowed_days},
        {'id': 5, 'name': 'Sat', 'checked': 5 in allowed_days},
        {'id': 6, 'name': 'Sun', 'checked': 6 in allowed_days},
    ]

    # Automated Daily Low-Leads Depletion Check
    from sender.alerts import check_and_send_low_leads_alert
    low_leads_info = check_and_send_low_leads_alert()

    # Lead Pool Metrics (Targeted vs Direct Draft Dumper)
    total_new_leads = Lead.objects.filter(status=Lead.STATUS_NEW).count()
    targeted_new = Lead.objects.filter(channel=Lead.CHANNEL_TARGETED, status=Lead.STATUS_NEW).count()
    direct_new = Lead.objects.filter(channel=Lead.CHANNEL_DIRECT, status=Lead.STATUS_NEW).count()

    context = {
        'setting': setting,
        'window_ok': window_ok,
        'window_reason': window_reason,
        'next_open_dt': next_open_dt,
        'sent_today': sent_today,
        'daily_cap': daily_cap,
        'cap_pct': cap_pct,
        'remaining_today': remaining_today,
        'total_queued': total_queued,
        'scheduled_campaigns': scheduled_campaigns,
        'is_worker_alive': is_worker_alive,
        'heartbeat_ago_str': heartbeat_ago_str,
        'gmail_cred': gmail_cred,
        'days_data': days_data,
        'local_now': local_now,
        'timezone_name': settings.TIME_ZONE,
        'low_leads_info': low_leads_info,
        'total_new_leads': total_new_leads,
        'targeted_new': targeted_new,
        'direct_new': direct_new,
    }
    return render(request, 'scheduler.html', context)


@ops_required
@require_POST
def update_scheduler_settings(request):
    """Save scheduler configuration from the dashboard."""
    setting = Setting.get()

    try:
        daily_cap = int(request.POST.get('daily_cap', setting.daily_cap))
        per_hour_cap_raw = request.POST.get('per_hour_cap', '').strip()
        per_hour_cap = int(per_hour_cap_raw) if per_hour_cap_raw else None

        min_delay = int(request.POST.get('min_delay_seconds', setting.min_delay_seconds))
        max_delay = int(request.POST.get('max_delay_seconds', setting.max_delay_seconds))
        min_days = int(request.POST.get('min_days_between_emails', setting.min_days_between_emails))
        bounce_pct = float(request.POST.get('bounce_pause_percent', setting.bounce_pause_percent))

        window_start_str = request.POST.get('window_start', '').strip()
        window_end_str = request.POST.get('window_end', '').strip()

        # Parse weekdays checkboxes
        selected_days = request.POST.getlist('weekdays')
        if not selected_days:
            messages.error(request, 'At least one sending weekday must be selected.')
            return redirect('scheduler_dashboard')

        send_weekdays = ','.join(sorted(selected_days))

        if min_delay > max_delay:
            messages.error(request, 'Minimum jitter delay cannot be greater than maximum delay.')
            return redirect('scheduler_dashboard')

        if window_start_str:
            parsed_start = parse_time(window_start_str)
            if parsed_start:
                setting.window_start = parsed_start

        if window_end_str:
            parsed_end = parse_time(window_end_str)
            if parsed_end:
                setting.window_end = parsed_end

        # Low lead alerts config
        low_lead_threshold_raw = request.POST.get('low_lead_threshold', '').strip()
        if low_lead_threshold_raw:
            setting.low_lead_threshold = max(5, int(low_lead_threshold_raw))

        alert_email = request.POST.get('alert_recipient_email', '').strip()
        setting.alert_recipient_email = alert_email

        setting.daily_cap = max(1, daily_cap)
        setting.per_hour_cap = per_hour_cap
        setting.min_delay_seconds = max(1, min_delay)
        setting.max_delay_seconds = max(1, max_delay)
        setting.min_days_between_emails = max(0, min_days)
        setting.bounce_pause_percent = max(0.5, min(100.0, bounce_pct))
        setting.send_weekdays = send_weekdays
        setting.save()

        messages.success(request, 'Scheduler settings and low lead alerts updated successfully.')
    except (ValueError, TypeError) as e:
        logger.error('Failed to update scheduler settings: %s', e)
        messages.error(request, 'Invalid value entered. Please check your inputs.')

    return redirect('scheduler_dashboard')


@ops_required
@require_POST
def toggle_scheduler_pause(request):
    """Toggle master scheduler pause / active switch."""
    setting = Setting.get()
    setting.paused = not setting.paused
    if setting.paused:
        setting.pause_reason = 'Manually paused via Scheduler Hub'
        setting.paused_at = timezone.now()
    else:
        setting.pause_reason = ''
        setting.paused_at = None

    setting.save(update_fields=['paused', 'pause_reason', 'paused_at', 'updated_at'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.content_type:
        return JsonResponse({
            'status': 'ok',
            'paused': setting.paused,
            'message': 'Engine paused' if setting.paused else 'Engine active and dispatching'
        })

    status_msg = 'Sending engine has been PAUSED.' if setting.paused else 'Sending engine is now ACTIVE.'
    messages.info(request, status_msg)
    return redirect('scheduler_dashboard')


@ops_required
@require_POST
def reschedule_campaign(request, pk):
    """Update start_at scheduled time for a specific campaign."""
    campaign = get_object_or_404(Campaign, pk=pk)
    datetime_str = request.POST.get('schedule_datetime', '').strip()

    if not datetime_str:
        messages.error(request, 'Please provide a valid date and time.')
        return redirect('scheduler_dashboard')

    parsed_dt = parse_datetime(datetime_str)
    if not parsed_dt:
        messages.error(request, 'Invalid date format.')
        return redirect('scheduler_dashboard')

    aware_dt = timezone.make_aware(parsed_dt) if timezone.is_naive(parsed_dt) else parsed_dt
    campaign.start_at = aware_dt
    if campaign.status in (Campaign.STATUS_DRAFT, Campaign.STATUS_PAUSED):
        campaign.status = Campaign.STATUS_SCHEDULED

    campaign.save(update_fields=['start_at', 'status'])

    dt_str = aware_dt.astimezone(get_app_tz()).strftime('%d %b %Y at %I:%M %p IST')
    messages.success(request, f'Campaign "{campaign.name}" rescheduled for {dt_str}.')
    return redirect('scheduler_dashboard')


@ops_required
@require_POST
def run_campaign_now(request, pk):
    """Instantly promote a scheduled campaign to send right now."""
    campaign = get_object_or_404(Campaign, pk=pk)
    campaign.start_at = timezone.now()
    campaign.status = Campaign.STATUS_SCHEDULED
    campaign.save(update_fields=['start_at', 'status'])

    # Make sure engine is not paused
    setting = Setting.get()
    if setting.paused:
        messages.warning(
            request,
            f'Campaign "{campaign.name}" start time moved to NOW. Note: The master engine is currently PAUSED — unpause it to begin delivery.'
        )
    else:
        messages.success(
            request,
            f'Campaign "{campaign.name}" queued to send immediately! The engine worker will pick it up on the next pass.'
        )

    return redirect('scheduler_dashboard')


@ops_required
@require_POST
def toggle_campaign_pause(request, pk):
    """Pause or resume an individual campaign without affecting others."""
    campaign = get_object_or_404(Campaign, pk=pk)
    if campaign.status == Campaign.STATUS_PAUSED:
        campaign.status = Campaign.STATUS_SCHEDULED if campaign.start_at and campaign.start_at > timezone.now() else Campaign.STATUS_SENDING
        messages.success(request, f'Campaign "{campaign.name}" resumed.')
    elif campaign.status in (Campaign.STATUS_SCHEDULED, Campaign.STATUS_SENDING):
        campaign.status = Campaign.STATUS_PAUSED
        messages.info(request, f'Campaign "{campaign.name}" paused.')
    else:
        messages.warning(request, f'Campaign status is {campaign.get_status_display()} and cannot be paused/resumed.')

    campaign.save(update_fields=['status'])
    return redirect('scheduler_dashboard')


@ops_required
@require_POST
def process_queue_now(request):
    """Trigger an immediate sending pass from the scheduler UI."""
    try:
        result = run_once(max_seconds=30, dry_run=False)
        sent = result.get('sent', 0)
        blocked = result.get('blocked_reason', '')
        remaining = result.get('remaining', 0)

        if blocked:
            messages.warning(request, f'Engine pass completed: {blocked}')
        else:
            messages.success(
                request,
                f'Engine pass complete: {sent} email(s) dispatched. {remaining} email(s) remaining in queue.'
            )
    except Exception as e:
        logger.error('Manual process queue error: %s', e, exc_info=True)
        messages.error(request, f'Engine execution error: {str(e)}')

    return redirect('scheduler_dashboard')


@ops_required
def live_dispatch_stream_api(request):
    """
    Streaming HTTP endpoint (NDJSON) for the Live Dispatch Console.
    Dispatches emails in real-time, yielding live status chunks to drive the UI animation.
    """
    def event_stream():
        import time as py_time
        from sender.models import Suppression
        from leads.models import Lead
        from sender.gmail_sender import send_email
        from sender.engine import _render_body, _get_unsub_url
        from django.db import close_old_connections

        close_old_connections()
        start_today, end_today = today_bounds()

        # Extract parameters
        campaign_id = request.GET.get('campaign_id') or request.POST.get('campaign_id')
        try:
            batch_size = int(request.GET.get('batch_size') or request.POST.get('batch_size') or 20)
        except (ValueError, TypeError):
            batch_size = 20
        batch_size = max(1, min(50, batch_size))

        yield json.dumps({'type': 'init', 'message': '🚀 Initializing Live Dispatch Engine...'}) + '\n'

        # Gmail credentials check
        cred = GmailCredential.get_active()
        if not cred:
            yield json.dumps({'type': 'error', 'message': '❌ No Gmail account connected. Go to Settings → Gmail to connect.'}) + '\n'
            return

        yield json.dumps({'type': 'auth', 'message': f'🔑 Authenticated with Gmail: {cred.email}'}) + '\n'

        # Find or select campaign
        campaign = None
        if campaign_id:
            campaign = Campaign.objects.filter(id=campaign_id).first()

        if not campaign:
            campaign = Campaign.objects.filter(
                status__in=[Campaign.STATUS_SCHEDULED, Campaign.STATUS_SENDING]
            ).order_by('-created_at').first() or Campaign.objects.order_by('-created_at').first()

        if not campaign:
            yield json.dumps({'type': 'error', 'message': '❌ No active campaign found to dispatch.'}) + '\n'
            return

        yield json.dumps({
            'type': 'campaign_info',
            'campaign_id': campaign.id,
            'campaign_name': campaign.name,
            'subject': campaign.subject,
            'message': f'📋 Active Campaign: "{campaign.name}"'
        }) + '\n'

        # Check queued send logs for this campaign
        queued_logs = list(
            SendLog.objects.filter(campaign=campaign, status=SendLog.STATUS_QUEUED)
            .select_related('lead')[:batch_size]
        )

        # If campaign has 0 queued rows, auto-populate from available leads so the user can test immediately!
        if not queued_logs:
            yield json.dumps({
                'type': 'info',
                'message': '🔍 Campaign has 0 queued recipients. Pulling leads from database...'
            }) + '\n'

            suppressed_emails = set(Suppression.objects.values_list('email', flat=True))
            # First try un-contacted leads
            available_leads = list(
                Lead.objects.exclude(email__in=suppressed_emails)
                .exclude(send_logs__campaign=campaign)
                .filter(status=Lead.STATUS_NEW)[:batch_size]
            )
            # If still needed, any non-suppressed lead
            if len(available_leads) < batch_size:
                needed = batch_size - len(available_leads)
                already_ids = [l.id for l in available_leads]
                more = list(
                    Lead.objects.exclude(email__in=suppressed_emails)
                    .exclude(id__in=already_ids)[:needed]
                )
                available_leads.extend(more)

            if available_leads:
                new_logs = [
                    SendLog(campaign=campaign, lead=l, status=SendLog.STATUS_QUEUED)
                    for l in available_leads
                ]
                SendLog.objects.bulk_create(new_logs, ignore_conflicts=True)
                campaign.planned_total = max(campaign.planned_total or 0, len(available_leads))
                campaign.status = Campaign.STATUS_SENDING
                campaign.save(update_fields=['planned_total', 'status'])

                queued_logs = list(
                    SendLog.objects.filter(campaign=campaign, status=SendLog.STATUS_QUEUED)
                    .select_related('lead')[:batch_size]
                )
                yield json.dumps({
                    'type': 'info',
                    'message': f'✓ Queued {len(queued_logs)} recipient(s) for "{campaign.name}".'
                }) + '\n'

        if not queued_logs:
            yield json.dumps({
                'type': 'error',
                'message': '❌ No leads available in the system. Please upload a CSV or add leads in Leads.'
            }) + '\n'
            return

        total_to_send = len(queued_logs)
        yield json.dumps({
            'type': 'start',
            'total': total_to_send,
            'message': f'⚡ Dispatching batch of {total_to_send} emails live...'
        }) + '\n'

        sent_count = 0
        failed_count = 0

        for i, log in enumerate(queued_logs):
            lead = log.lead
            recipient_name = lead.name or lead.email.split('@')[0].capitalize()

            yield json.dumps({
                'type': 'sending',
                'index': i + 1,
                'total': total_to_send,
                'email': lead.email,
                'name': recipient_name,
                'message': f'✉️ [{i+1}/{total_to_send}] Dispatching to {lead.email}...'
            }) + '\n'

            try:
                unsub_url = _get_unsub_url(lead)
                body_text = _render_body(campaign.body_text or 'Proposal details', lead, unsub_url)
                body_html = _render_body(campaign.body_html or f'<p>{body_text}</p>', lead, unsub_url)
                subject = _render_body(campaign.subject or 'Proposal', lead, unsub_url)

                send_res = send_email(
                    credential=cred,
                    to_email=lead.email,
                    to_name=recipient_name,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    unsub_url=unsub_url,
                )

                if isinstance(send_res, (list, tuple)):
                    gmail_id = str(send_res[0]) if len(send_res) > 0 else ''
                    msg_id_hdr = str(send_res[1]) if len(send_res) > 1 else ''
                else:
                    gmail_id = str(send_res)
                    msg_id_hdr = ''

                log.status = SendLog.STATUS_SENT
                log.sent_at = timezone.now()
                log.gmail_message_id = gmail_id
                log.message_id_header = msg_id_hdr
                log.save(update_fields=['status', 'sent_at', 'gmail_message_id', 'message_id_header'])

                lead.status = Lead.STATUS_CONTACTED
                lead.last_contacted_at = timezone.now()
                lead.save(update_fields=['status', 'last_contacted_at'])

                sent_count += 1
                curr_sent_today = SendLog.objects.filter(
                    status=SendLog.STATUS_SENT, sent_at__range=(start_today, end_today)
                ).count()

                disp_id = (gmail_id[:16] + '...') if gmail_id else 'OK'
                yield json.dumps({
                    'type': 'sent',
                    'index': i + 1,
                    'total': total_to_send,
                    'email': lead.email,
                    'msg_id': disp_id,
                    'sent_today': curr_sent_today,
                    'message': f'✓ [{i+1}/{total_to_send}] SENT to {lead.email}'
                }) + '\n'

            except Exception as exc:
                err_str = str(exc)
                logger.error('Live dispatch send error for %s: %s', lead.email, exc)
                log.status = SendLog.STATUS_FAILED
                log.error = err_str[:255]
                log.sent_at = timezone.now()
                log.save(update_fields=['status', 'error', 'sent_at'])
                failed_count += 1

                yield json.dumps({
                    'type': 'failed',
                    'index': i + 1,
                    'total': total_to_send,
                    'email': lead.email,
                    'error': err_str,
                    'message': f'⚠️ [{i+1}/{total_to_send}] Failed: {err_str[:80]}'
                }) + '\n'

            # Pacing delay between emails (1.2 seconds for live animation visibility)
            if i + 1 < total_to_send:
                yield json.dumps({
                    'type': 'pacing',
                    'delay': 1.2,
                    'message': '⏳ Anti-spam jitter pacing: 1.2s delay...'
                }) + '\n'
                py_time.sleep(1.2)

        # Update campaign status
        remaining_in_camp = SendLog.objects.filter(campaign=campaign, status=SendLog.STATUS_QUEUED).count()
        if remaining_in_camp == 0:
            campaign.status = Campaign.STATUS_DONE
            campaign.finished_at = timezone.now()
            campaign.save(update_fields=['status', 'finished_at'])

        final_sent_today = SendLog.objects.filter(
            status=SendLog.STATUS_SENT, sent_at__range=(start_today, end_today)
        ).count()

        yield json.dumps({
            'type': 'done',
            'sent': sent_count,
            'failed': failed_count,
            'sent_today': final_sent_today,
            'remaining_in_campaign': remaining_in_camp,
            'message': f'🎉 Live Dispatch Complete: {sent_count} email(s) sent! Sent Today: {final_sent_today}'
        }) + '\n'

    response = StreamingHttpResponse(event_stream(), content_type='application/x-ndjson')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response

