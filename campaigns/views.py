"""
campaigns/views.py — Campaign list, detail, approve, pause, cancel.
Fixes D1 (status gating), D2 (frequency cap), D10 (chunked bulk_create), D11 (RBAC).
"""
import logging
from datetime import timedelta

from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Count, Q

from accounts.rbac import ops_required, admin_required
from .models import Campaign
from sender.models import SendLog, Suppression, Setting
from leads.models import Lead

logger = logging.getLogger(__name__)


@ops_required
def campaigns_list(request):
    campaigns = Campaign.objects.select_related('created_by', 'approved_by').annotate(
        sent_c=Count('send_logs', filter=Q(send_logs__status='sent')),
        queued_c=Count('send_logs', filter=Q(send_logs__status='queued')),
    ).all()
    status_filter = request.GET.get('status', '')
    if status_filter:
        campaigns = campaigns.filter(status=status_filter)

    return render(request, 'campaigns/list.html', {
        'campaigns': campaigns,
        'status_filter': status_filter,
        'status_choices': Campaign.STATUS_CHOICES,
    })


@ops_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    logs = campaign.send_logs.select_related('lead').order_by('-created_at')[:50]

    audience = _get_audience_queryset(campaign)
    suppressed = set(Suppression.objects.values_list('email', flat=True))
    eligible = audience.exclude(email__in=suppressed)

    setting = Setting.get()
    min_days = setting.min_days_between_emails
    freq_cutoff = timezone.now() - timedelta(days=min_days)
    freq_capped_count = eligible.filter(
        send_logs__status=SendLog.STATUS_SENT,
        send_logs__sent_at__gte=freq_cutoff,
    ).distinct().count()

    return render(request, 'campaigns/detail.html', {
        'campaign': campaign,
        'logs': logs,
        'audience_count': audience.count(),
        'eligible_count': eligible.count() - freq_capped_count,
        'suppressed_count': audience.count() - eligible.count(),
        'freq_capped_count': freq_capped_count,
    })


@admin_required
@require_POST
def campaign_approve(request, pk):
    """
    Admin only: approve a pending campaign and build its queue.
    D1: sets status to 'scheduled' (not 'approved').
    D2: Excludes leads contacted within min_days_between_emails.
    D10: Uses chunked bulk_create instead of Python loop.
    """
    campaign = get_object_or_404(Campaign, pk=pk)

    if not campaign.can_approve:
        messages.error(
            request,
            f'Cannot approve: inbox-safety score is {campaign.spam_score}/10 '
            f'(need ≥ 7), or this campaign is not in "Pending Approval" status.'
        )
        return redirect('campaign_detail', pk=pk)

    # D8: Block approve if score is explicitly below 7
    if campaign.spam_score is not None and campaign.spam_score < 7:
        messages.error(
            request,
            f'Inbox-safety score ({campaign.spam_score}/10) is too low to approve. '
            f'Fix the issues listed in the editor first.'
        )
        return redirect('campaign_detail', pk=pk)

    # Event reference check
    if campaign.audience_event:
        if campaign.audience_event.lower() not in (campaign.body_text or '').lower():
            messages.error(
                request,
                f'Safety check: the email body does not mention the event '
                f'"{campaign.audience_event}". Edit the draft and reference it in the first line.'
            )
            return redirect('campaign_detail', pk=pk)

    # D2: Frequency cap — find leads recently contacted by ANY campaign
    setting = Setting.get()
    min_days = setting.min_days_between_emails
    freq_cutoff = timezone.now() - timedelta(days=min_days)
    recently_contacted_ids = set(
        SendLog.objects.filter(
            status=SendLog.STATUS_SENT,
            sent_at__gte=freq_cutoff,
        ).values_list('lead_id', flat=True)
    )

    campaign.status = Campaign.STATUS_SCHEDULED
    campaign.approved_by = request.user
    campaign.approved_at = timezone.now()
    campaign.save()

    # D10: Build queue efficiently with chunked bulk_create
    audience = _get_audience_queryset(campaign)
    suppressed_emails = set(Suppression.objects.values_list('email', flat=True))
    existing_lead_ids = set(
        SendLog.objects.filter(campaign=campaign).values_list('lead_id', flat=True)
    )

    batch_size = 1000
    total_queued = 0
    total_skipped_suppressed = 0
    total_skipped_freq = 0
    batch = []

    for lead in audience.values_list('id', 'email').iterator(chunk_size=2000):
        lead_id, email = lead
        if email in suppressed_emails:
            total_skipped_suppressed += 1
            continue
        if lead_id in existing_lead_ids:
            continue
        if lead_id in recently_contacted_ids:
            total_skipped_freq += 1
            continue
        batch.append(SendLog(campaign=campaign, lead_id=lead_id, status=SendLog.STATUS_QUEUED))
        if len(batch) >= batch_size:
            SendLog.objects.bulk_create(batch, ignore_conflicts=True)
            total_queued += len(batch)
            batch = []

    if batch:
        SendLog.objects.bulk_create(batch, ignore_conflicts=True)
        total_queued += len(batch)

    # Record planned total
    campaign.planned_total = total_queued
    campaign.save(update_fields=['planned_total'])

    messages.success(
        request,
        f'Campaign scheduled! {total_queued:,} emails queued '
        f'({total_skipped_suppressed:,} suppressed, {total_skipped_freq:,} contacted recently).'
    )
    return redirect('campaign_detail', pk=pk)


@ops_required
@require_POST
def campaign_pause(request, pk):
    """Pause or resume a campaign. Pausing does NOT delete queue rows."""
    campaign = get_object_or_404(Campaign, pk=pk)

    if campaign.status in (Campaign.STATUS_SCHEDULED, Campaign.STATUS_SENDING):
        campaign.status = Campaign.STATUS_PAUSED
        campaign.paused_reason = request.POST.get('reason', 'Manually paused')
        campaign.save()
        messages.success(request, 'Campaign paused. Queued emails will not be sent until resumed.')

    elif campaign.status == Campaign.STATUS_PAUSED:
        campaign.status = Campaign.STATUS_SCHEDULED
        campaign.paused_reason = ''
        campaign.save()
        messages.success(request, 'Campaign resumed. It will send in the next scheduled window.')

    else:
        messages.error(request, f'Cannot pause/resume campaign in status: {campaign.get_status_display()}')

    return redirect('campaign_detail', pk=pk)


@admin_required
@require_POST
def campaign_cancel(request, pk):
    """Cancel a campaign. Marks its queued rows as skipped."""
    campaign = get_object_or_404(Campaign, pk=pk)

    if campaign.status in (Campaign.STATUS_DONE, Campaign.STATUS_CANCELLED):
        messages.error(request, 'Campaign is already finished or cancelled.')
        return redirect('campaign_detail', pk=pk)

    # Cancel queued rows
    cancelled_count = SendLog.objects.filter(
        campaign=campaign, status=SendLog.STATUS_QUEUED
    ).update(
        status=SendLog.STATUS_SKIPPED,
        error='Campaign cancelled by admin',
        sent_at=timezone.now(),
    )

    campaign.status = Campaign.STATUS_CANCELLED
    campaign.finished_at = timezone.now()
    campaign.save()

    messages.success(request, f'Campaign cancelled. {cancelled_count:,} queued rows skipped.')
    return redirect('campaign_detail', pk=pk)


def _get_audience_queryset(campaign):
    """Build filtered lead queryset for a campaign."""
    qs = Lead.objects.all()
    if campaign.audience_event:
        qs = qs.filter(source__icontains=campaign.audience_event)
    if campaign.audience_status:
        qs = qs.filter(status=campaign.audience_status)
    if campaign.target_category and campaign.target_category != Campaign.CAT_ALL:
        qs = qs.filter(category=campaign.target_category)
    qs = qs.exclude(status__in=[Lead.STATUS_UNSUBSCRIBED, Lead.STATUS_BOUNCED])
    return qs
