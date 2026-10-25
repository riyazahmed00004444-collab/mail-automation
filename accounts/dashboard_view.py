"""
accounts/dashboard_view.py — Main dashboard.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from sender.timeutils import today_bounds
from sender.models import SendLog, Suppression, GmailCredential, Setting
from sender.alerts import check_and_send_low_leads_alert
from campaigns.models import Campaign
from leads.models import Lead


@login_required
def dashboard(request):
    start_today, end_today = today_bounds()

    sent_today = SendLog.objects.filter(
        status=SendLog.STATUS_SENT,
        sent_at__range=(start_today, end_today)
    ).count()

    bounced_today = SendLog.objects.filter(
        status__in=[SendLog.STATUS_BOUNCED, SendLog.STATUS_FAILED],
        sent_at__range=(start_today, end_today)
    ).count()

    queue_depth   = SendLog.objects.filter(status=SendLog.STATUS_QUEUED).count()
    suppression_count = Suppression.objects.count()

    setting    = Setting.get()
    gmail_cred = GmailCredential.get_active()

    recent_campaigns = Campaign.objects.select_related('created_by').order_by('-created_at')[:8]

    # --- Lead pipeline stats ---
    total_leads      = Lead.objects.count()
    contacted_leads  = Lead.objects.filter(status=Lead.STATUS_CONTACTED).count()
    new_leads        = Lead.objects.filter(status=Lead.STATUS_NEW).count()
    targeted_new     = Lead.objects.filter(channel=Lead.CHANNEL_TARGETED, status=Lead.STATUS_NEW).count()
    direct_new       = Lead.objects.filter(channel=Lead.CHANNEL_DIRECT, status=Lead.STATUS_NEW).count()
    suppressed_leads = Lead.objects.filter(status__in=[
        Lead.STATUS_UNSUBSCRIBED, Lead.STATUS_BOUNCED
    ]).count()

    # Total sent ever
    total_sent_ever = SendLog.objects.filter(status=SendLog.STATUS_SENT).count()

    # Days left at current cap (based on uncontacted new leads)
    daily_cap    = setting.daily_cap or 50
    remaining    = new_leads  # leads not yet contacted
    days_left    = (remaining // daily_cap) if daily_cap > 0 else 0

    # Low leads alert & daily email dispatch
    low_leads_info = check_and_send_low_leads_alert()
    low_leads_alert = low_leads_info['is_low']

    # Overall progress %
    progress_pct = int((contacted_leads / total_leads) * 100) if total_leads > 0 else 0

    context = {
        # Today's stats
        'sent_today':         sent_today,
        'daily_cap':          daily_cap,
        'cap_pct':            min(100, int(sent_today / daily_cap * 100)) if daily_cap else 0,
        'bounced_today':      bounced_today,

        # Queue
        'queue_depth':        queue_depth,
        'suppression_count':  suppression_count,

        # System state
        'paused':             setting.paused,
        'gmail_connected':    gmail_cred is not None,
        'gmail_email':        gmail_cred.email if gmail_cred else None,
        'auto_paused':        setting.paused and bounced_today > 4,

        # Lead pipeline
        'total_leads':        total_leads,
        'contacted_leads':    contacted_leads,
        'new_leads':          new_leads,
        'targeted_new':       targeted_new,
        'direct_new':         direct_new,
        'suppressed_leads':   suppressed_leads,
        'total_sent_ever':    total_sent_ever,
        'remaining':          remaining,
        'days_left':          days_left,
        'progress_pct':       progress_pct,
        'low_leads_alert':    low_leads_alert,
        'low_leads_info':     low_leads_info,

        'recent_campaigns':   recent_campaigns,
    }
    return render(request, 'dashboard.html', context)
