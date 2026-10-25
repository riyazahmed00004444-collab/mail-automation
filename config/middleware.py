"""
config/middleware.py — Custom middleware for BidAlert Comms Hub.
"""
from django.conf import settings
from django.middleware.csrf import get_token


class ForceCsrfCookieMiddleware:
    """
    1. Guarantees the CSRF cookie is set on EVERY response.
    2. When settings.DEBUG is True (default local development), relaxes strict
       CSRF enforcement so local testing/browsing never fails with 'CSRF token missing'.
       In production (DEBUG=False), full CSRF enforcement remains active.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # In development mode, don't block requests with CSRF missing/mismatch
        if getattr(settings, 'DEBUG', False):
            setattr(request, '_dont_enforce_csrf_checks', True)

        # Still issue the CSRF cookie so templates and JS fetch calls have a token
        try:
            get_token(request)
        except Exception:
            pass

        response = self.get_response(request)
        return response
