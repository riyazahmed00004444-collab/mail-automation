"""
sender/log_views.py — Full audit trail of sends.
"""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.core.paginator import Paginator
from django.db.models import Q

from .models import SendLog


@login_required
def logs_list(request):
    qs = SendLog.objects.select_related('campaign', 'lead').order_by('-created_at')

    q = request.GET.get('q', '')
    status_filter = request.GET.get('status', '')
    campaign_filter = request.GET.get('campaign', '')

    if q:
        qs = qs.filter(
            Q(lead__email__icontains=q) |
            Q(campaign__name__icontains=q) |
            Q(lead__name__icontains=q)
        )
    if status_filter:
        qs = qs.filter(status=status_filter)
    if campaign_filter:
        qs = qs.filter(campaign_id=campaign_filter)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'logs/list.html', {
        'page_obj': page,
        'q': q,
        'status_filter': status_filter,
        'status_choices': SendLog.STATUS_CHOICES,
        'total': qs.count(),
        'cron_secret': settings.CRON_SECRET,
    })
