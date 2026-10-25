"""
campaigns/tracking_view.py — Dedicated campaign tracking & analytics page.
"""
from datetime import date, timedelta
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Count, Q

from .models import Campaign
from sender.models import SendLog, Setting
from leads.models import Lead


from sender.timeutils import today_bounds, now_local, get_app_tz


@login_required
def tracking(request):
    tz = get_app_tz()
    local_now = now_local()
    start_today, end_today = today_bounds()

    # --- Per-campaign stats ---
    campaigns = Campaign.objects.annotate(
        total_q   = Count('send_logs'),
        sent_c    = Count('send_logs', filter=Q(send_logs__status='sent')),
        failed_c  = Count('send_logs', filter=Q(send_logs__status__in=['failed', 'bounced'])),
        queued_c  = Count('send_logs', filter=Q(send_logs__status='queued')),
        skipped_c = Count('send_logs', filter=Q(send_logs__status='skipped')),
    ).order_by('-created_at')

    # Attach progress % to each campaign
    campaign_rows = []
    for c in campaigns:
        total = c.total_q or 1
        pct   = int((c.sent_c / total) * 100)
        campaign_rows.append({
            'campaign': c,
            'sent':     c.sent_c,
            'queued':   c.queued_c,
            'failed':   c.failed_c,
            'skipped':  c.skipped_c,
            'total':    c.total_q,
            'pct':      pct,
        })

    # --- Overall pipeline ---
    setting          = Setting.get()
    total_leads      = Lead.objects.count()
    contacted        = Lead.objects.filter(status=Lead.STATUS_CONTACTED).count()
    new_leads        = Lead.objects.filter(status=Lead.STATUS_NEW).count()
    suppressed_leads = Lead.objects.filter(
        status__in=[Lead.STATUS_UNSUBSCRIBED, Lead.STATUS_BOUNCED]
    ).count()
    progress_pct     = int((contacted / total_leads) * 100) if total_leads else 0
    daily_cap        = setting.daily_cap or 50
    days_left        = new_leads // daily_cap if daily_cap else 0

    # --- Last 7 days send history (timezone-aware) ---
    history = []
    for i in range(6, -1, -1):
        d_day = local_now - timedelta(days=i)
        d_start = d_day.replace(hour=0, minute=0, second=0, microsecond=0)
        d_end = d_day.replace(hour=23, minute=59, second=59, microsecond=999999)
        sent  = SendLog.objects.filter(status='sent', sent_at__range=(d_start, d_end)).count()
        fail  = SendLog.objects.filter(status__in=['failed', 'bounced'], sent_at__range=(d_start, d_end)).count()
        history.append({'label': d_day.strftime('%d %b'), 'sent': sent, 'fail': fail})

    # --- Lead pools (Targeted vs Direct Draft Dumper) ---
    targeted_new = Lead.objects.filter(channel=Lead.CHANNEL_TARGETED, status=Lead.STATUS_NEW).count()
    direct_new   = Lead.objects.filter(channel=Lead.CHANNEL_DIRECT, status=Lead.STATUS_NEW).count()

    from sender.alerts import check_and_send_low_leads_alert
    low_leads_info = check_and_send_low_leads_alert()

    # --- Category breakdown of leads ---
    cat_breakdown = (
        Lead.objects
        .values('category')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    cat_labels  = [d['category'].title() for d in cat_breakdown]
    cat_counts  = [d['count'] for d in cat_breakdown]

    # --- Sent today (timezone-aware IST bounds) ---
    sent_today     = SendLog.objects.filter(status='sent', sent_at__range=(start_today, end_today)).count()
    bounced_today  = SendLog.objects.filter(
        status__in=['bounced', 'failed'], sent_at__range=(start_today, end_today)
    ).count()
    total_sent_ever = SendLog.objects.filter(status='sent').count()

    return render(request, 'campaigns/tracking.html', {
        'campaign_rows':    campaign_rows,
        'total_leads':      total_leads,
        'contacted':        contacted,
        'new_leads':        new_leads,
        'targeted_new':     targeted_new,
        'direct_new':       direct_new,
        'suppressed_leads': suppressed_leads,
        'progress_pct':     progress_pct,
        'days_left':        days_left,
        'daily_cap':        daily_cap,
        'history':          history,
        'cat_labels':       cat_labels,
        'cat_counts':       cat_counts,
        'sent_today':       sent_today,
        'bounced_today':    bounced_today,
        'total_sent_ever':  total_sent_ever,
        'low_leads':        low_leads_info['is_low'],
        'low_leads_info':   low_leads_info,
    })
