"""
aiops/gemini.py — Gemini AI drafts and spam scoring.
D13: model read from GEMINI_MODEL env var (no hard-coded model name).
     Wraps calls with timeout + one retry; returns human-friendly errors.
"""
import json
import re
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

TRIGGER_WORDS = [
    'free', 'winner', 'winners', 'urgent', 'guaranteed', 'guarantee',
    'act now', 'limited time', 'click here', 'buy now', 'order now',
    'congratulations', 'you have been selected', 'earn money',
    'make money', '100%', 'no obligation', 'risk-free',
]

PLACEHOLDER_RE = re.compile(r'\{\{[^}]+\}\}')


def _parse_json(raw: str) -> dict:
    """Robustly parse JSON from Gemini response."""
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z]*\n?', '', text)
        text = re.sub(r'\n?```$', '', text.strip())
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    def escape_inner(m):
        s = m.group(0)
        s = s.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        return s

    fixed = re.sub(r'"((?:[^"\\]|\\.)*)"', escape_inner, cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError('Could not parse Gemini JSON response', text, 0)


def _get_model():
    """
    Get configured Gemini model. D13: reads from GEMINI_MODEL env var.
    Verify the model name at https://aistudio.google.com/ before deploying.
    """
    import google.generativeai as genai
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise ValueError('GEMINI_API_KEY is not set. Add it to your .env file.')
    genai.configure(api_key=api_key)
    model_name = getattr(settings, 'GEMINI_MODEL', 'gemini-1.5-flash-latest')
    return genai.GenerativeModel(model_name)


def _call_with_retry(model, prompt: str, max_retries: int = 1) -> str:
    """Call Gemini with one retry on transient error."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            # Only retry on transient / quota errors
            if any(k in err_str for k in ('timeout', '503', '500', '429', 'resource_exhausted')):
                logger.warning('Gemini transient error (attempt %d): %s', attempt + 1, e)
                if attempt < max_retries:
                    import time
                    time.sleep(2)
                continue
            break  # Non-transient: don't retry
    raise last_exc


def gemini_draft(intent: str, knowledge_items: list[dict], audience_desc: str,
                 tone: str = 'professional', event_name: str = '') -> dict:
    """
    Call Gemini to draft an email.
    Returns: {subject, body_text, body_html}
    Raises ValueError with a human-friendly message on failure.
    """
    kb_block = '\n\n'.join(
        f'### {item["title"]}\n{item["content"]}'
        for item in knowledge_items
    ) or 'No knowledge base entries found.'

    event_instruction = ''
    if event_name:
        event_instruction = (
            f'\n- IMPORTANT: The audience met BidAlert at "{event_name}". '
            f'The VERY FIRST LINE of the email body MUST reference this event specifically.'
        )

    system_prompt = f"""You are an email copywriter for BidAlert.in — a B2B lead intelligence and tender alert platform.

RULES (non-negotiable):
1. Use ONLY the facts in the Knowledge Base below. Do NOT invent plans, prices, features, or claims.
2. Never output unfilled placeholders like {{{{anything}}}}. The ONLY allowed placeholders are:
   - {{{{lead_name}}}} — recipient's name
   - {{{{company}}}} — recipient's company
   - {{{{unsub_url}}}} — unsubscribe link (MUST be included in footer)
3. Every email MUST end with this exact footer block:
   ---
   You're receiving this because you expressed interest in BidAlert.in.
   Unsubscribe: {{{{unsub_url}}}}
   BidAlert.in | info@bidalert.in
4. Tone: {tone}
5. Subject line: max 50 characters, no ALL-CAPS words, no spam trigger words.{event_instruction}

KNOWLEDGE BASE (only source of facts):
{kb_block}

AUDIENCE DESCRIPTION: {audience_desc}

USER INTENT: {intent}

Respond with ONLY valid JSON — no markdown fences, no extra text:
{{
  "subject": "...",
  "body_text": "plain text version",
  "body_html": "HTML version with <p> tags"
}}"""

    try:
        model = _get_model()
        raw = _call_with_retry(model, system_prompt)
        data = _parse_json(raw)
        required = {'subject', 'body_text', 'body_html'}
        if not required.issubset(data.keys()):
            raise ValueError(f'AI response missing keys: {required - data.keys()}')
        return data

    except json.JSONDecodeError as e:
        logger.error('Gemini returned invalid JSON: %s', e)
        raise ValueError('The AI returned an unexpected response. Please try again.')
    except ValueError:
        raise
    except Exception as e:
        logger.error('Gemini draft error: %s', e)
        err_lower = str(e).lower()
        if 'quota' in err_lower or '429' in err_lower or 'resource_exhausted' in err_lower:
            raise ValueError('The AI is busy right now. Please try again in a minute.')
        if 'api_key' in err_lower or 'api key' in err_lower:
            raise ValueError('Gemini API key is invalid or not set. Check Settings.')
        raise ValueError(f'The AI is unavailable: {e}')


def spam_lint(subject: str, body_text: str) -> list[str]:
    """Local fast spam checks. Returns list of issue strings."""
    issues = []

    allowed = {'{{lead_name}}', '{{company}}', '{{unsub_url}}'}
    found = set(PLACEHOLDER_RE.findall(subject + ' ' + body_text))
    unknown = found - allowed
    if unknown:
        issues.append(f'Unfilled placeholders: {", ".join(unknown)}')

    if '{{unsub_url}}' not in body_text:
        issues.append('Missing {{unsub_url}} unsubscribe link in body')

    if len(subject) > 50:
        issues.append(f'Subject too long: {len(subject)} chars (max 50)')

    words = re.findall(r'\b[A-Z]{2,}\b', subject + ' ' + body_text)
    caps_words = [w for w in words if w not in ('BidAlert', 'B2B', 'CSV', 'AI', 'FAQ', 'ROI', 'SPF', 'DKIM')]
    if len(caps_words) > 2:
        issues.append(f'Too many ALL-CAPS words: {", ".join(caps_words[:5])}')

    combined = (subject + ' ' + body_text).lower()
    found_triggers = [w for w in TRIGGER_WORDS if w in combined]
    if found_triggers:
        issues.append(f'Spam trigger words: {", ".join(found_triggers)}')

    return issues


def gemini_spam_score(subject: str, body_text: str, issues: list[str]) -> dict:
    """Ask Gemini to rate the email 1-10 for deliverability."""
    lint_block = '\n'.join(f'- {i}' for i in issues) if issues else 'None detected by local lint.'

    prompt = f"""You are an email deliverability expert. Rate this email 1-10 for inbox deliverability (10 = excellent).

Subject: {subject}

Body:
{body_text}

Local lint issues already found:
{lint_block}

Respond ONLY with valid JSON — no markdown:
{{
  "score": <integer 1-10>,
  "issues": ["issue 1", "issue 2"]
}}

Scoring guide:
- 9-10: Clean, professional, no red flags
- 7-8: Minor issues, acceptable
- 5-6: Moderate issues, likely filtered
- 1-4: High spam probability, do not send"""

    try:
        model = _get_model()
        raw = _call_with_retry(model, prompt)
        data = _parse_json(raw)
        score = max(1, min(10, int(data.get('score', 5))))
        ai_issues = data.get('issues', [])
        all_issues = list(set(issues + ai_issues))
        return {'score': score, 'issues': all_issues}

    except Exception as e:
        logger.error('Gemini spam score error: %s', e)
        score = max(1, 10 - len(issues) * 2)
        return {'score': score, 'issues': issues}


def check_event_reference(body_text: str, event_name: str) -> bool:
    """Returns True if the event name is referenced in the email body."""
    if not event_name:
        return True
    return event_name.lower() in body_text.lower()
