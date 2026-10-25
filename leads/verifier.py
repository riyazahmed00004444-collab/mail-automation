"""
leads/verifier.py — Email syntax and domain MX verification.
Fixed D5: removed duplicate function stub, use DB cache (DomainCheck),
system resolver first (PA-safe), disposable domains from text file.
"""
import re
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# ── Syntax regex ──────────────────────────────────────────────────────────────
EMAIL_SYNTAX_REGEX = re.compile(
    r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}$'
)

# ── Role/abuse local-part heuristics (warning, not hard block) ────────────────
ROLE_ADDRESSES = {
    'abuse', 'postmaster', 'mailer-daemon', 'noreply', 'no-reply',
    'donotreply', 'do-not-reply', 'bounce', 'bounces', 'return',
    'returns', 'spam', 'junk', 'trash', 'webmaster',
}

# ── Dummy / Fake email patterns to weed out non-real leads ────────────────────
FAKE_LOCAL_PATTERNS = {
    'test', 'fake', 'dummy', 'asdf', 'sample', 'placeholder', 'none', 'null',
    'temp', 'example', 'demo', 'xyz', 'abc', '123', '1234', '12345', '123456',
    'admin', 'user', 'noemail', 'notreal', 'qwerty', 'email', 'mail', 'test1', 'test2',
    'someone', 'anybody', 'nobody', 'na', 'n/a', 'unknown', 'testuser', 'demo1',
}

FAKE_DOMAINS = {
    'test.com', 'example.com', 'example.org', 'example.net', 'fake.com', 'sample.com',
    'dummy.com', 'asdf.com', 'xyz.com', 'abc.com', 'none.com', 'domain.com',
    'email.com', 'somedomain.com', 'mysite.com', 'temp.com', 'website.com',
    'fakemail.com', 'nomail.com', 'myemail.com', 'test.in', 'fake.in',
}

COMMON_DOMAIN_TYPOS = {
    'gamil.com': 'gmail.com',
    'gmaill.com': 'gmail.com',
    'gmai.com': 'gmail.com',
    'gmial.com': 'gmail.com',
    'gemail.com': 'gmail.com',
    'yaho.com': 'yahoo.com',
    'yahooo.com': 'yahoo.com',
    'hotmial.com': 'hotmail.com',
    'outlok.com': 'outlook.com',
    'redifmail.com': 'rediffmail.com',
}

GIBBERISH_RE = re.compile(r'^[a-z0-9]{20,}$')  # 20+ char all-lowercase alphanumeric

# ── Known major providers & government domains (always trusted, skip DNS) ─────
TRUSTED_DOMAINS = {
    'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.co.in', 'yahoo.co.uk',
    'yahoo.ca', 'yahoo.fr', 'hotmail.com', 'hotmail.co.uk', 'hotmail.fr',
    'outlook.com', 'outlook.in', 'live.com', 'live.co.uk', 'live.fr',
    'msn.com', 'icloud.com', 'me.com', 'mac.com', 'aol.com', 'zoho.com',
    'proton.me', 'protonmail.com', 'rediffmail.com', 'bidalert.in',
    # Indian Government & Institutional domains
    'gov.in', 'nic.in', 'gem.gov.in', 'railnet.gov.in', 'isro.gov.in', 'drdo.gov.in',
    'iitb.ac.in', 'iitd.ac.in', 'iitm.ac.in', 'iitk.ac.in',
}

# Provider prefix fast-check (catches yahoo.de, hotmail.es, etc.)
MAJOR_PROVIDER_PREFIXES = {
    'gmail', 'googlemail', 'yahoo', 'ymail', 'rocketmail',
    'hotmail', 'outlook', 'live', 'msn', 'passport',
    'icloud', 'me', 'mac', 'zoho', 'proton', 'protonmail',
    'aol', 'aim', 'gmx', 'web', 'freenet', 't-online',
    'mail', 'yandex', 'rambler', 'inbox', 'rediff', 'rediffmail', 'bidalert',
}

# ── Disposable domain list (from file) ───────────────────────────────────────
_DISPOSABLE_DOMAINS: set[str] | None = None


def _load_disposable_domains() -> set[str]:
    global _DISPOSABLE_DOMAINS
    if _DISPOSABLE_DOMAINS is None:
        data_file = Path(__file__).parent / 'data' / 'disposable_domains.txt'
        try:
            lines = data_file.read_text(encoding='utf-8').splitlines()
            _DISPOSABLE_DOMAINS = {line.strip().lower() for line in lines if line.strip()}
        except Exception as e:
            logger.warning('Could not load disposable_domains.txt: %s', e)
            _DISPOSABLE_DOMAINS = set()
    return _DISPOSABLE_DOMAINS


# ── Clean & auto-correct email address ────────────────────────────────────────

def clean_email(email: str) -> tuple[str, bool]:
    """Clean whitespace, strip quotes, lowercase, and fix common domain typos."""
    if not email:
        return '', False
    cleaned = email.strip().strip('\'"<>,;()[]').lower()
    corrected = False
    if '@' in cleaned:
        local, domain = cleaned.rsplit('@', 1)
        if domain in COMMON_DOMAIN_TYPOS:
            domain = COMMON_DOMAIN_TYPOS[domain]
            cleaned = f"{local}@{domain}"
            corrected = True
    return cleaned, corrected


# ── Syntax & Real Email validation ────────────────────────────────────────────

def validate_email_syntax(email: str) -> Tuple[bool, str]:
    """Validate email format and weed out fake / placeholder addresses. Returns (ok, reason)."""
    if not email or not isinstance(email, str):
        return False, 'Empty email address'

    email, _ = clean_email(email)
    if len(email) > 254:
        return False, 'Email exceeds 254 characters'

    if '..' in email:
        return False, 'Consecutive dots in email address'

    if not EMAIL_SYNTAX_REGEX.match(email):
        return False, 'Invalid email address format'

    local_part, domain = email.rsplit('@', 1)
    domain = domain.lower()
    local_lower = local_part.lower()

    if local_part.startswith('.') or local_part.endswith('.'):
        return False, 'Local part starts or ends with dot'

    if len(local_part) < 2:
        return False, 'Local part too short (less than 2 characters)'

    # Repeated characters (e.g., aaaa@, 1111@)
    if re.match(r'^(.)\1{3,}$', local_lower):
        return False, 'Repetitive dummy local part'

    # Check fake local part patterns
    if local_lower in FAKE_LOCAL_PATTERNS:
        return False, f'Placeholder / test email address ({local_part}@...)'

    # Check fake domains
    if domain in FAKE_DOMAINS or domain.startswith('example.') or domain.startswith('test.'):
        return False, f'Test / dummy domain ({domain})'

    disposable = _load_disposable_domains()
    if domain in disposable or domain.endswith('.yopmail.net') or domain.endswith('.tempmail.com'):
        return False, f'Disposable / temporary email domain ({domain})'

    return True, 'OK'


def check_role_address(email: str) -> Tuple[bool, str]:
    """
    Warning (not hard block): detect role addresses and gibberish local parts.
    Returns (is_role_or_gibberish, reason). Caller decides whether to skip.
    """
    local_part = email.rsplit('@', 1)[0].lower()
    if local_part in ROLE_ADDRESSES:
        return True, f'Role address ({local_part}@…) — low deliverability expected'
    if GIBBERISH_RE.match(local_part):
        return True, f'Possibly gibberish local part: {local_part}'
    return False, ''


# ── DB-cached MX check ────────────────────────────────────────────────────────

def _check_mx_live(domain: str) -> Tuple[bool, str]:
    """
    Live DNS MX lookup. Uses system resolver first; falls back to public DNS.
    Timeout 2.5s to stay fast.
    """
    try:
        import dns.resolver
    except ImportError:
        logger.warning('dnspython not installed; MX check disabled (returning True)')
        return True, 'dnspython not installed (allowed)'

    try:
        resolver = dns.resolver.Resolver(configure=True)
        resolver.lifetime = 2.5
        resolver.timeout = 2.0

        try:
            answers = resolver.resolve(domain, 'MX')
            if answers:
                return True, 'MX records found'
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            try:
                a_answers = resolver.resolve(domain, 'A')
                if a_answers:
                    return True, 'A record fallback'
            except Exception:
                pass
            return False, f'No MX or A records for domain "{domain}"'
        except dns.resolver.NXDOMAIN:
            return False, f'Domain "{domain}" does not exist'
        except dns.resolver.LifetimeTimeout:
            return True, 'DNS timed out (allowed)'

    except Exception as e:
        logger.warning('DNS error for %s: %s', domain, e)
        return True, f'DNS error (allowed): {e}'

    return True, 'OK'


def check_domain_mx(domain: str) -> Tuple[bool, str]:
    """
    Check domain MX, using DB cache (DomainCheck, 30-day TTL).
    Falls back to live DNS if cache misses or is stale.
    """
    domain = domain.strip().lower()

    # Fast-path: trusted domain / government / major provider
    if domain in TRUSTED_DOMAINS or domain.endswith('.gov.in') or domain.endswith('.nic.in') or domain.endswith('.ac.in'):
        return True, 'Trusted / government domain'
    prefix = domain.split('.')[0]
    if prefix in MAJOR_PROVIDER_PREFIXES:
        return True, f'Trusted provider family ({prefix})'

    # Fast-path: disposable
    disposable = _load_disposable_domains()
    if domain in disposable:
        return False, 'Disposable email domain'

    # DB cache
    try:
        from sender.models import DomainCheck
        try:
            record = DomainCheck.objects.get(domain=domain)
            if not record.is_stale:
                return record.ok, record.reason
        except DomainCheck.DoesNotExist:
            pass

        ok, reason = _check_mx_live(domain)

        DomainCheck.objects.update_or_create(
            domain=domain,
            defaults={'ok': ok, 'reason': reason},
        )
        return ok, reason

    except Exception as db_err:
        logger.debug('DB cache unavailable for MX check: %s', db_err)
        return _check_mx_live(domain)


# ── Full verification ─────────────────────────────────────────────────────────

def verify_email(email: str) -> Tuple[bool, str]:
    """
    Full verification: syntax → dummy filter → MX check.
    Returns (is_valid, reason).
    """
    email, _ = clean_email(email)
    ok, reason = validate_email_syntax(email)
    if not ok:
        return False, reason

    domain = email.rsplit('@', 1)[1].strip().lower()
    return check_domain_mx(domain)


# ── Bulk Real Lead Sorter Engine ──────────────────────────────────────────────

def sort_real_leads(raw_items: list, check_mx: bool = True) -> tuple[list[dict], list[dict], dict]:
    """
    Takes a raw list of items (either dicts with 'email', 'name', etc., or raw email strings)
    and sorts them into:
      1. real_leads: genuine, deliverable emails ready for storage
      2. discarded_leads: invalid, fake, disposable, or dead-domain emails with reason
      3. stats: counts breakdown for the UI dumper
    """
    real_leads = []
    discarded_leads = []
    seen_in_batch = set()

    stats = {
        'total': len(raw_items),
        'real': 0,
        'fake': 0,
        'disposable': 0,
        'invalid_syntax': 0,
        'mx_failed': 0,
        'duplicates': 0,
    }

    disposable_set = _load_disposable_domains()

    for item in raw_items:
        if isinstance(item, str):
            raw_email = item
            row_data = {'email': raw_email, 'name': '', 'company': '', 'phone': '', 'source': ''}
        else:
            raw_email = item.get('email', '')
            row_data = dict(item)

        cleaned_em, _ = clean_email(raw_email)

        if not cleaned_em or '@' not in cleaned_em:
            stats['invalid_syntax'] += 1
            discarded_leads.append({
                'email': raw_email,
                'reason': 'Empty or malformed address',
                'category': 'syntax'
            })
            continue

        # In-file duplicate check
        if cleaned_em in seen_in_batch:
            stats['duplicates'] += 1
            discarded_leads.append({
                'email': cleaned_em,
                'reason': 'Duplicate address in file',
                'category': 'duplicate'
            })
            continue

        # Syntax & dummy validation
        ok_syntax, syntax_reason = validate_email_syntax(cleaned_em)
        if not ok_syntax:
            if 'Disposable' in syntax_reason:
                stats['disposable'] += 1
                cat = 'disposable'
            elif 'Placeholder' in syntax_reason or 'dummy' in syntax_reason or 'Repetitive' in syntax_reason:
                stats['fake'] += 1
                cat = 'fake'
            else:
                stats['invalid_syntax'] += 1
                cat = 'syntax'
            discarded_leads.append({
                'email': cleaned_em,
                'reason': syntax_reason,
                'category': cat
            })
            continue

        local_part, domain = cleaned_em.rsplit('@', 1)

        # Fast disposable check
        if domain in disposable_set or domain.endswith('.yopmail.net'):
            stats['disposable'] += 1
            discarded_leads.append({
                'email': cleaned_em,
                'reason': f'Disposable temporary inbox ({domain})',
                'category': 'disposable'
            })
            continue

        # MX Verification
        if check_mx:
            ok_mx, mx_reason = check_domain_mx(domain)
            if not ok_mx:
                stats['mx_failed'] += 1
                discarded_leads.append({
                    'email': cleaned_em,
                    'reason': mx_reason,
                    'category': 'mx'
                })
                continue

        # Real, valid lead!
        seen_in_batch.add(cleaned_em)
        row_data['email'] = cleaned_em
        real_leads.append(row_data)
        stats['real'] += 1

    return real_leads, discarded_leads, stats
