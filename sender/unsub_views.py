"""
sender/unsub_views.py — RFC 8058 one-click unsubscribe.
No login required. Signed token via django.core.signing.
"""
import logging
from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .models import Suppression
from leads.models import Lead

logger = logging.getLogger(__name__)


@csrf_exempt
def unsubscribe_view(request, token):
    """
    GET  → show confirmation page
    POST → one-click unsubscribe (RFC 8058) or button click
    """
    try:
        email = signing.loads(token, salt=settings.UNSUB_SIGNING_SALT, max_age=60 * 60 * 24 * 365)
    except signing.BadSignature:
        return render(request, 'unsub/invalid.html', status=400)
    except signing.SignatureExpired:
        return render(request, 'unsub/expired.html', status=400)

    if request.method == 'POST':
        # Add to suppression
        Suppression.objects.get_or_create(
            email=email,
            defaults={'reason': Suppression.REASON_UNSUB}
        )
        # Update lead status
        Lead.objects.filter(email=email).update(status=Lead.STATUS_UNSUBSCRIBED)

        logger.info('Unsubscribed: %s', email)

        # RFC 8058: one-click expects 200
        return render(request, 'unsub/confirmed.html', {'email': email})

    # GET — show confirmation page
    return render(request, 'unsub/confirm.html', {
        'email': email,
        'token': token,
    })
