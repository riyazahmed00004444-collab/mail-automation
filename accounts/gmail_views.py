"""
accounts/gmail_views.py — Gmail OAuth connect flow.
"""
import os
import logging
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from django.conf import settings
from google_auth_oauthlib.flow import Flow

logger = logging.getLogger(__name__)

# Allow OAuth over plain HTTP on local dev (never set this in production)
if settings.DEBUG:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'


SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
]


def _build_flow():
    return Flow.from_client_config(
        {
            'web': {
                'client_id': settings.GOOGLE_CLIENT_ID,
                'client_secret': settings.GOOGLE_CLIENT_SECRET,
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
                'redirect_uris': [settings.GOOGLE_OAUTH_REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
    )


@login_required
def gmail_connect(request):
    if not request.user.is_staff:
        messages.error(request, 'Staff access required.')
        return redirect('dashboard')

    flow = _build_flow()
    auth_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent',
        include_granted_scopes='true',
    )
    request.session['gmail_oauth_state'] = state
    return redirect(auth_url)


@login_required
def gmail_callback(request):
    if not request.user.is_staff:
        messages.error(request, 'Staff access required.')
        return redirect('dashboard')

    state = request.session.get('gmail_oauth_state')
    if not state:
        messages.error(request, 'OAuth state missing. Please try again.')
        return redirect('settings')

    flow = _build_flow()
    flow.fetch_token(
        authorization_response=request.build_absolute_uri(),
        state=state,
    )
    credentials = flow.credentials

    # Get email from the ID token (avoids extra API call & scope issues)
    import requests as http_requests
    token_info = http_requests.get(
        'https://www.googleapis.com/oauth2/v3/userinfo',
        headers={'Authorization': f'Bearer {credentials.token}'}
    ).json()
    email = token_info.get('email', '')

    from sender.models import GmailCredential
    from django.utils import timezone
    from datetime import timezone as dt_tz
    GmailCredential.objects.update_or_create(
        email=email,
        defaults={
            'access_token': credentials.token,
            'refresh_token': credentials.refresh_token or '',
            'token_expiry': credentials.expiry.replace(tzinfo=dt_tz.utc) if credentials.expiry else None,
            'scopes': ' '.join(credentials.scopes or SCOPES),
        }
    )

    messages.success(request, f'Gmail connected: {email}')
    return redirect('settings')


@login_required
def gmail_disconnect(request):
    if not request.user.is_staff:
        messages.error(request, 'Staff access required.')
        return redirect('dashboard')

    from sender.models import GmailCredential
    GmailCredential.objects.all().delete()
    messages.success(request, 'Gmail disconnected.')
    return redirect('settings')
