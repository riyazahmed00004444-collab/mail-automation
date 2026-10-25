"""
sender/gmail_sender.py — Gmail API send logic with OAuth token refresh,
error classification (D4), Reply-To, display name, and Message-ID headers.
"""
import base64
import logging
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone as dt_tz

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# ── Error classification (D4) ─────────────────────────────────────────────────
ERROR_CLASS_PERMANENT = 'permanent'
ERROR_CLASS_TRANSIENT = 'transient'
ERROR_CLASS_QUOTA = 'quota'
ERROR_CLASS_AUTH = 'auth'


def classify_error(exc) -> str:
    """
    Classify a Gmail API (or network) exception into one of four categories.

    permanent  → invalid recipient (5xx address errors) → bounce + suppress lead
    transient  → network / DNS / server timeouts → retry with backoff
    quota      → HTTP 429 / rateLimitExceeded / daily limit → stop whole run
    auth       → invalid_grant / 401 → stop run, flag credential for reconnect
    """
    msg = str(exc).lower()

    # Auth failures
    if any(k in msg for k in ('invalid_grant', 'token has been expired',
                               'token has been revoked', '401',
                               'request had invalid authentication')):
        return ERROR_CLASS_AUTH

    # Quota / rate limit
    if any(k in msg for k in ('429', 'ratelimitexceeded', 'userratelimitexceeded',
                               'daily limit', 'quota exceeded', 'too many requests')):
        return ERROR_CLASS_QUOTA

    # Permanent recipient errors (5xx SMTP, or Gmail "invalid" / "does not exist")
    permanent_indicators = [
        'invalid', 'does not exist', 'no such user', 'user unknown',
        'mailbox not found', 'mailbox unavailable', 'address rejected',
        '550', '551', '552', '553', '554',
        'recipient address rejected', 'address does not exist',
        'permanent failure',
    ]
    if any(k in msg for k in permanent_indicators):
        return ERROR_CLASS_PERMANENT

    # Everything else is transient: network error, 5xx from Google itself, etc.
    return ERROR_CLASS_TRANSIENT


def is_bounce_error(error_msg: str) -> bool:
    """Legacy helper — prefer classify_error() for new code."""
    return classify_error(Exception(error_msg)) == ERROR_CLASS_PERMANENT


# ── Gmail service builder ─────────────────────────────────────────────────────

def _build_service(credential):
    """Build Gmail API service from stored credential, refreshing if needed."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    scopes_list = credential.scopes.split() if isinstance(credential.scopes, str) else list(credential.scopes)
    creds = Credentials(
        token=credential.access_token,
        refresh_token=credential.refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=scopes_list,
    )

    if creds.expired or not creds.token:
        creds.refresh(Request())
        credential.access_token = creds.token
        credential.token_expiry = creds.expiry.replace(tzinfo=dt_tz.utc) if creds.expiry else None
        credential.needs_reauth = False
        credential.save()

    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


# ── MIME message builder ──────────────────────────────────────────────────────

def build_mime_message(
    to_email: str,
    to_name: str,
    from_email: str,
    subject: str,
    body_text: str,
    body_html: str = '',
    unsub_url: str = '',
    message_id_header: str = '',
) -> MIMEMultipart:
    """Build MIME email with proper unsubscribe, Reply-To, and Message-ID headers."""
    msg = MIMEMultipart('alternative')
    msg['To'] = f'{to_name} <{to_email}>' if to_name else to_email

    display_name = getattr(settings, 'SENDER_DISPLAY_NAME', 'BidAlert')
    msg['From'] = f'{display_name} <{from_email}>'

    reply_to = getattr(settings, 'REPLY_TO_EMAIL', '')
    if reply_to:
        msg['Reply-To'] = reply_to

    msg['Subject'] = subject
    if unsub_url:
        msg['List-Unsubscribe'] = f'<{unsub_url}>, <mailto:{from_email}?subject=unsubscribe>'
        msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'

    # Message-ID for idempotency (D7): generated before send, stored on SendLog
    if not message_id_header:
        try:
            base_domain = getattr(settings, 'BASE_URL', 'localhost').split('//')[1].rstrip('/')
        except Exception:
            base_domain = 'bidalert.in'
        message_id_header = f'<{uuid.uuid4().hex}@{base_domain}>'
    msg['Message-ID'] = message_id_header

    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    if body_html:
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    return msg


# ── Send ──────────────────────────────────────────────────────────────────────

def send_email(
    credential,
    to_email: str,
    to_name: str,
    subject: str,
    body_text: str,
    body_html: str = '',
    unsub_url: str = '',
    message_id_header: str = '',
) -> tuple[str, str]:
    """
    Send one email via Gmail API.
    Returns (gmail_message_id, message_id_header) on success.
    Raises on failure — caller uses classify_error() to decide how to handle.
    """
    service = _build_service(credential)
    if not message_id_header:
        try:
            base_domain = settings.BASE_URL.split('//')[1].rstrip('/')
        except Exception:
            base_domain = 'bidalert.in'
        message_id_header = f'<{uuid.uuid4().hex}@{base_domain}>'

    msg = build_mime_message(
        to_email=to_email,
        to_name=to_name,
        from_email=credential.email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        unsub_url=unsub_url,
        message_id_header=message_id_header,
    )
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
    result = service.users().messages().send(
        userId='me',
        body={'raw': raw}
    ).execute()
    return result.get('id', ''), message_id_header
