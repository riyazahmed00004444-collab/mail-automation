"""
accounts/setup_view.py — First-time setup wizard.
Shows only when: no Gmail connected OR no Setting exists.
"""
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
import json

from sender.models import GmailCredential, Setting
from leads.models import Lead
from campaigns.models import Campaign


def setup_required(request):
    """Check if setup wizard should be shown."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return False
    gmail = GmailCredential.get_active()
    return gmail is None


@csrf_exempt
@login_required
def setup_wizard(request):
    if not request.user.is_staff:
        return redirect('dashboard')

    setting = Setting.get()
    gmail = GmailCredential.get_active()

    # Steps completion status
    steps = {
        'gmail':    gmail is not None,
        'cap':      setting.daily_cap != 50,  # D14: removed 'or True' placeholder
        'leads':    Lead.objects.exists(),
        'campaign': Campaign.objects.exists(),
    }
    all_done = steps['gmail'] and steps['leads']

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_cap':
            try:
                cap = int(request.POST.get('daily_cap', 50))
                batch = int(request.POST.get('batch_size', 25))
                setting.daily_cap = max(1, min(500, cap))
                setting.batch_size = max(1, min(50, batch))
                setting.save()
                messages.success(request, f'Daily cap set to {cap}, batch size {batch}.')
            except Exception:
                messages.error(request, 'Invalid values.')
            return redirect('setup_wizard')

    return render(request, 'accounts/setup_wizard.html', {
        'steps': steps,
        'all_done': all_done,
        'setting': setting,
        'gmail': gmail,
    })
