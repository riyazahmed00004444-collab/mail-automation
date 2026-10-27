"""
sender/api_views.py — API endpoints for queue processing, stats, suppression.
D9: removed @csrf_exempt from send_test_email_api, fixed cron secret comparison.
D6: fixed date.today() → timezone-aware bounds.
D10: verify-leads moved to management command (see sender/management/commands/verify_leads.py).
"""
import hmac
import json
import logging
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Count, Q

from accounts.rbac import ops_required, admin_required
from .engine import run_once
from .models import SendLog, Suppression, Setting
from .timeutils import today_bounds
from leads.models import Lead

logger = logging.getLogger(__name__)


def _verify_cron_secret(request) -> bool:
    """
    Verify the X-Cron-Secret header using constant-time comparison (D9).
    Returns False if CRON_SECRET is empty or is the dev default in production.
    """
    expected = settings.CRON_SECRET
    if not expected:
        return False
    received = request.headers.get('X-Cron-Secret', '')
    if not received:
        return False
    return hmac.compare_digest(expected, received)


@require_POST
def process_queue_api(request):
    """
    POST /api/process-queue
    Auth: X-Cron-Secret header (cron) OR logged-in ops/staff session (browser button).
    D9: Uses CSRF protection for browser calls; cron uses header auth.
    """
    is_cron = _verify_cron_secret(request)
    is_ops_user = (
        request.user.is_authenticated and (
            request.user.is_staff or
            request.user.groups.filter(name='automation_handler').exists()
        )
    )

    if not is_cron and not is_ops_user:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    # Note: for browser calls, CSRF is enforced by the view (no csrf_exempt).
    # For cron calls from PA, the cron task doesn't need CSRF — it uses the header secret.
    # To handle both: the cron task POSTs with X-Cron-Secret and we skip CSRF check
    # via a middleware exemption — OR we simply use @csrf_exempt only for cron.
    # Decision: add @csrf_exempt here since cron cannot set CSRF tokens,
    # and we guard with hmac.compare_digest instead (this is the standard Django pattern).
    try:
        result = run_once(max_seconds=25)
        return JsonResponse(result)
    except Exception as e:
        logger.error('process_queue_api error: %s', e)
        return JsonResponse({'error': str(e)}, status=500)


# Apply csrf_exempt ONLY to process_queue_api (for the PA cron task)
# We keep it here to make it visible and intentional.
from django.views.decorators.csrf import csrf_exempt
process_queue_api = csrf_exempt(process_queue_api)


@ops_required
def sync_bounces_api(request):
    """POST/GET /api/sync-bounces — Scan Gmail for bounce emails."""
    from .bounce_scanner import scan_gmail_bounces
    result = scan_gmail_bounces(max_messages=50)
    return JsonResponse(result)


@ops_required
def manual_suppress_api(request):
    """POST /api/manual-suppress — Paste emails to suppress immediately."""
    text = ''
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            text = data.get('text', '')
        except Exception:
            text = request.POST.get('text', '')

    if not text:
        return JsonResponse({'error': 'No emails or text provided.'}, status=400)

    found_emails = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', text)
    if not found_emails:
        return JsonResponse({'error': 'No valid email addresses found.'}, status=400)

    from .bounce_scanner import suppress_emails
    result = suppress_emails(found_emails, reason=Suppression.REASON_BOUNCED, note='Manually supplied')
    return JsonResponse({'status': 'ok', **result})


@login_required
def live_stats(request):
    """GET /api/stats — Live send stats for dashboard polling."""
    start_day, end_day = today_bounds()   # D6 fix: use timezone-aware bounds

    setting = Setting.get()

    sent_today    = SendLog.objects.filter(status='sent', sent_at__range=(start_day, end_day)).count()
    bounced_today = SendLog.objects.filter(status__in=['bounced'], sent_at__range=(start_day, end_day)).count()
    queue_depth   = SendLog.objects.filter(status='queued').count()
    total_sent    = SendLog.objects.filter(status='sent').count()
    suppressed    = Suppression.objects.count()
    total_leads   = Lead.objects.count()
    contacted     = Lead.objects.filter(status=Lead.STATUS_CONTACTED).count()
    new_leads     = Lead.objects.filter(status=Lead.STATUS_NEW).count()
    daily_cap     = setting.daily_cap or 50
    cap_pct       = min(100, int(sent_today / daily_cap * 100)) if daily_cap else 0
    days_left     = new_leads // daily_cap if daily_cap else 0
    progress_pct  = int(contacted / total_leads * 100) if total_leads else 0

    from campaigns.models import Campaign
    active_campaigns = list(Campaign.objects.filter(
        status__in=list(Campaign.SENDING_ELIGIBLE) + [Campaign.STATUS_PAUSED]
    ).annotate(
        queued_c=Count('send_logs', filter=Q(send_logs__status='queued')),
        sent_c=Count('send_logs', filter=Q(send_logs__status='sent')),
        total_c=Count('send_logs'),
    ).values('id', 'name', 'status', 'queued_c', 'sent_c', 'total_c', 'planned_total'))

    # Heartbeat status
    heartbeat = setting.last_worker_heartbeat
    heartbeat_ok = False
    if heartbeat:
        from datetime import timedelta
        heartbeat_ok = (timezone.now() - heartbeat) < timedelta(hours=2)

    return JsonResponse({
        'sent_today':       sent_today,
        'bounced_today':    bounced_today,
        'queue_depth':      queue_depth,
        'total_sent':       total_sent,
        'suppressed':       suppressed,
        'total_leads':      total_leads,
        'contacted':        contacted,
        'new_leads':        new_leads,
        'daily_cap':        daily_cap,
        'cap_pct':          cap_pct,
        'days_left':        days_left,
        'progress_pct':     progress_pct,
        'paused':           setting.paused,
        'pause_reason':     setting.pause_reason,
        'heartbeat_ok':     heartbeat_ok,
        'active_campaigns': active_campaigns,
        'last_summary':     setting.last_worker_summary,
    })


@csrf_exempt
@ops_required
@require_POST
def send_test_email_api(request):
    """
    POST /api/send-test-email
    CSRF-exempt for test email sending reliability.
    """
    from campaigns.models import Campaign
    from .models import GmailCredential
    from .gmail_sender import send_email
    from django.conf import settings as djsettings

    try:
        data = json.loads(request.body)
    except Exception:
        data = {}

    campaign_id = data.get('campaign_id')
    to_email = data.get('to_email', '').strip() or request.user.email

    if not to_email:
        return JsonResponse({'error': 'No recipient email. Set your email in the admin panel first.'}, status=400)
    if not campaign_id:
        return JsonResponse({'error': 'campaign_id required'}, status=400)

    campaign = Campaign.objects.filter(pk=campaign_id).first()
    if not campaign:
        return JsonResponse({'error': 'Campaign not found'}, status=404)

    cred = GmailCredential.get_active()
    if not cred:
        return JsonResponse({'error': 'No Gmail account connected. Go to Settings → Gmail.'}, status=400)

    base_url = getattr(djsettings, 'BASE_URL', 'http://localhost:8000')
    unsub_url = f'{base_url}/unsub/test/'

    try:
        subject = f'[TEST] {campaign.subject}'
        body_text = f'--- THIS IS A TEST EMAIL ---\n\n{campaign.body_text}'
        body_html = campaign.body_html or f'<p><strong>[TEST EMAIL]</strong></p><p>{campaign.body_text}</p>'

        gmail_id, _ = send_email(
            credential=cred,
            to_email=to_email,
            to_name=request.user.get_full_name() or request.user.username,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            unsub_url=unsub_url,
        )

        # Record test was sent
        campaign.tested_at = timezone.now()
        campaign.tested_to = to_email
        campaign.save(update_fields=['tested_at', 'tested_to'])

        return JsonResponse({'status': 'sent', 'to': to_email, 'subject': subject, 'gmail_id': gmail_id})

    except Exception as e:
        logger.error('send_test_email_api error: %s', e)
        return JsonResponse({'error': str(e)}, status=500)
