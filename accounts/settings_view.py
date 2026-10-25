"""
accounts/settings_view.py — App settings: cap, pause, Gmail info.
"""
import os
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.http import JsonResponse

from sender.models import Setting, GmailCredential


@login_required
def settings_view(request):
    if not request.user.is_staff:
        messages.error(request, 'Staff access required.')
        return redirect('dashboard')

    setting = Setting.get()
    gmail_cred = GmailCredential.get_active()

    required_vars = [
        'DATABASE_URL', 'SECRET_KEY', 'GOOGLE_CLIENT_ID',
        'GOOGLE_CLIENT_SECRET', 'GEMINI_API_KEY',
        'CRON_SECRET', 'BASE_URL', 'DEBUG',
    ]
    env_vars = [
        {'name': v, 'set': bool(os.environ.get(v, '').strip())}
        for v in required_vars
    ]
    warmup_schedule = [
        {'label': 'Week 1', 'cap': 50,   'pct': 5},
        {'label': 'Week 2', 'cap': 100,  'pct': 10},
        {'label': 'Week 3', 'cap': 200,  'pct': 20},
        {'label': 'Week 4', 'cap': 500,  'pct': 50},
        {'label': 'Week 6', 'cap': 1000, 'pct': 100},
    ]
    has_readonly = bool(gmail_cred and 'gmail.readonly' in (gmail_cred.scopes or ''))
    return render(request, 'settings.html', {
        'setting': setting,
        'gmail_cred': gmail_cred,
        'has_readonly': has_readonly,
        'env_vars': env_vars,
        'warmup_schedule': warmup_schedule,
    })


@login_required
@require_POST
def toggle_pause(request):
    if not request.user.is_staff:
        return JsonResponse({'error': 'Staff only'}, status=403)

    setting = Setting.get()
    setting.paused = not setting.paused
    setting.save()
    return JsonResponse({'paused': setting.paused})


@login_required
@require_POST
def update_cap(request):
    if not request.user.is_staff:
        messages.error(request, 'Staff access required.')
        return redirect('settings')

    setting = Setting.get()
    try:
        new_cap = int(request.POST.get('daily_cap', setting.daily_cap))
        new_batch = int(request.POST.get('batch_size', setting.batch_size))
        # Volume ramp guard: max 20% increase
        max_allowed = int(setting.daily_cap * setting.weekly_cap_bump)
        if new_cap > max_allowed:
            messages.error(request, f'Cap cannot exceed {max_allowed} (20% weekly ramp limit). Current cap: {setting.daily_cap}')
        else:
            setting.daily_cap = new_cap
            setting.batch_size = max(1, min(50, new_batch))
            setting.save()
            messages.success(request, 'Settings updated.')
    except (ValueError, TypeError):
        messages.error(request, 'Invalid values.')

    return redirect('settings')
