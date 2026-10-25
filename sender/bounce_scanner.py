"""
sender/bounce_scanner.py — Automatic Gmail bounce detection & lead suppression.
Scans inbox for Mail Delivery Subsystem / mailer-daemon failure notifications,
extracts the dead recipient email address, and marks them as Bounced & Suppressed.
"""
import re
import base64
import logging
from typing import List, Dict, Any, Set
from django.utils import timezone
from .models import GmailCredential, Suppression, SendLog, Setting
from .gmail_sender import _build_service
from leads.models import Lead

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')

# Specific regexes targeting bounce notification formats
BOUNCE_PATTERNS = [
    re.compile(r"Your message wasn't delivered to\s+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE),
    re.compile(r"Final-Recipient:\s*(?:rfc822;)?\s*<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?", re.IGNORECASE),
    re.compile(r"X-Failed-Recipients:\s*<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?", re.IGNORECASE),
    re.compile(r"(?:was not delivered to|delivery to the following recipient(?:s)? failed)\s*:?\s*<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?", re.IGNORECASE),
    re.compile(r"<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>:\s*(?:55\d|mailbox unavailable|user unknown|address not found|does not exist)", re.IGNORECASE),
]


def extract_bounced_email_from_text(text: str) -> Set[str]:
    """Extract failed recipient email addresses from bounce notification body/snippet."""
    found: Set[str] = set()
    for pat in BOUNCE_PATTERNS:
        for match in pat.findall(text):
            cleaned = match.strip().rstrip('>').lstrip('<').lower()
            if cleaned and '@' in cleaned:
                found.add(cleaned)
    return found


def extract_body_parts(payload: dict) -> str:
    """Recursively extract text content from Gmail message payload."""
    texts = []
    
    # Check headers
    headers = {h.get('name', '').lower(): h.get('value', '') for h in payload.get('headers', [])}
    if 'x-failed-recipients' in headers:
        texts.append(f"X-Failed-Recipients: {headers['x-failed-recipients']}")
    
    # Check body data
    body = payload.get('body', {})
    if body.get('data'):
        try:
            decoded = base64.urlsafe_b64decode(body['data']).decode('utf-8', errors='ignore')
            texts.append(decoded)
        except Exception:
            pass

    # Recurse parts
    for part in payload.get('parts', []):
        mime_type = part.get('mimeType', '')
        if mime_type.startswith('text/') or mime_type.startswith('message/'):
            texts.append(extract_body_parts(part))

    return "\n".join(texts)


def suppress_emails(emails: List[str], reason: str = Suppression.REASON_BOUNCED, note: str = '') -> Dict[str, Any]:
    """
    Mark a list of email addresses as suppressed, update Lead and SendLog records.
    """
    suppressed_count = 0
    leads_updated = 0
    logs_updated = 0
    processed_emails = []

    for raw_email in emails:
        clean_email = raw_email.strip().lower()
        if not clean_email or '@' not in clean_email:
            continue

        processed_emails.append(clean_email)

        # 1. Add to Suppression list
        _, created = Suppression.objects.get_or_create(
            email=clean_email,
            defaults={
                'reason': reason,
                'notes': note or 'Auto-suppressed via bounce scanner',
            }
        )
        if created:
            suppressed_count += 1

        # 2. Update Lead status
        l_count = Lead.objects.filter(email__iexact=clean_email).update(status=Lead.STATUS_BOUNCED)
        leads_updated += l_count

        # 3. Update SendLog status
        sl_count = SendLog.objects.filter(lead__email__iexact=clean_email).update(
            status=SendLog.STATUS_BOUNCED,
            error=note or 'Recipient mailbox bounced / not found'
        )
        logs_updated += sl_count

    return {
        'suppressed_count': suppressed_count,
        'leads_updated': leads_updated,
        'logs_updated': logs_updated,
        'emails': processed_emails,
    }


def scan_gmail_bounces(max_messages: int = 50) -> Dict[str, Any]:
    """
    Connects to Gmail via API, searches for bounce reports from Mail Delivery Subsystem,
    parses failed email recipients, and suppresses them.
    """
    credential = GmailCredential.get_active()
    if not credential:
        return {'status': 'error', 'error': 'No active Gmail credential found.'}

    # Verify readonly scope is available
    scopes = credential.scopes or ''
    if 'gmail.readonly' not in scopes and 'gmail.modify' not in scopes and 'mail.google.com' not in scopes:
        return {
            'status': 'scope_missing',
            'error': 'Gmail read permission (gmail.readonly) not granted yet. Please re-connect Gmail in Settings.',
        }

    try:
        service = _build_service(credential)
    except Exception as e:
        return {'status': 'error', 'error': f'Failed to initialize Gmail service: {str(e)}'}

    # Search for bounce messages
    query = 'from:(mailer-daemon OR "Mail Delivery Subsystem")'
    try:
        resp = service.users().messages().list(userId='me', q=query, maxResults=max_messages).execute()
    except Exception as e:
        logger.error('Failed to list Gmail bounce messages: %s', e)
        return {'status': 'error', 'error': f'Gmail API query failed: {str(e)}'}

    messages = resp.get('messages', [])
    if not messages:
        return {'status': 'ok', 'messages_scanned': 0, 'bounces_found': 0, 'suppressed': 0, 'emails': []}

    all_bounced_emails: Set[str] = set()

    for msg_meta in messages:
        msg_id = msg_meta['id']
        try:
            msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            snippet = msg.get('snippet', '')
            payload = msg.get('payload', {})
            body_text = extract_body_parts(payload)
            combined_text = f"{snippet}\n{body_text}"

            found_emails = extract_bounced_email_from_text(combined_text)
            all_bounced_emails.update(found_emails)
        except Exception as e:
            logger.warning('Error parsing message %s: %s', msg_id, e)
            continue

    if all_bounced_emails:
        result = suppress_emails(
            list(all_bounced_emails),
            reason=Suppression.REASON_BOUNCED,
            note='Automatically detected from Gmail Delivery Failure notification'
        )
        return {
            'status': 'ok',
            'messages_scanned': len(messages),
            'bounces_found': len(all_bounced_emails),
            'suppressed_count': result['suppressed_count'],
            'leads_updated': result['leads_updated'],
            'logs_updated': result['logs_updated'],
            'emails': list(all_bounced_emails),
        }

    return {
        'status': 'ok',
        'messages_scanned': len(messages),
        'bounces_found': 0,
        'suppressed_count': 0,
        'leads_updated': 0,
        'logs_updated': 0,
        'emails': [],
    }
