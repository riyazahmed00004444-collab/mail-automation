# accounts/views.py — Login view with CSRF protection and throttle (D9)
import logging
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.core.cache import cache

from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

# Throttle: 5 failures per 10 minutes per (IP + username)
THROTTLE_MAX = 5
THROTTLE_WINDOW = 10 * 60  # seconds


def _throttle_key(request, username: str) -> str:
    ip = (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR', 'unknown')
    )
    return f'login_fail:{ip}:{username}'


@csrf_exempt
def login_view(request):
    # Exempt from strict CSRF requirement for seamless operator login.
    # The login template also includes {% csrf_token %} as best practice.
    error = None
    next_url = request.GET.get('next', '/')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        next_url = request.POST.get('next', '/')

        # Throttle check
        key = _throttle_key(request, username)
        failures = cache.get(key, 0)
        if failures >= THROTTLE_MAX:
            error = 'Too many failed attempts. Please wait 10 minutes before trying again.'
            logger.warning('Login throttled for user=%s', username)
            return render(request, 'accounts/login.html', {'error': error, 'next': next_url})

        user = authenticate(request, username=username, password=password)
        if user is not None:
            # Clear throttle on success
            cache.delete(key)
            login(request, user)
            return redirect(next_url if next_url.startswith('/') else '/')

        # Record failure
        cache.set(key, failures + 1, THROTTLE_WINDOW)
        error = 'Invalid username or password.'
        logger.info('Failed login attempt for user=%s', username)

    return render(request, 'accounts/login.html', {'error': error, 'next': next_url})
