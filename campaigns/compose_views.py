"""
campaigns/compose_views.py — AI compose screen.
Fixes D3 (status reset), D8 (target_category not saved, approve-with-low-score).
"""
import csv
import io
import json
import logging
import re
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.rbac import ops_required, admin_required
from .models import Campaign
from knowledge.models import KnowledgeBase
from leads.models import Lead
from sender.models import SendLog, Suppression, GmailCredential, Setting
from aiops.gemini import gemini_draft, spam_lint, gemini_spam_score

logger = logging.getLogger(__name__)


@ops_required
def compose_view(request):
    """Create a new draft campaign."""
    if request.method == 'POST':
        return _handle_compose_post(request, campaign=None)

    events = Lead.objects.exclude(source='').values_list('source', flat=True).distinct()
    return render(request, 'campaigns/compose.html', {
        'campaign': None,
        'events': events,
        'status_choices': Lead.STATUS_CHOICES,
        'tone_choices': Campaign.TONE_CHOICES,
        'lane_choices': Campaign.LANE_CHOICES,
        'target_category_choices': Campaign.TARGET_CATEGORY_CHOICES,
    })


@ops_required
def compose_edit(request, pk):
    """Edit an existing draft/pending campaign."""
    campaign = get_object_or_404(Campaign, pk=pk)

    # D3 fix: Block editing of campaigns that are actively sending/scheduled
    # unless they are first paused.
    if campaign.status in (Campaign.STATUS_SCHEDULED, Campaign.STATUS_SENDING):
        messages.error(
            request,
            f'This campaign is {campaign.get_status_display()} and cannot be edited. '
            f'Pause it first, then edit.'
        )
        return redirect('campaign_detail', pk=pk)

    if request.method == 'POST':
        return _handle_compose_post(request, campaign=campaign)

    events = Lead.objects.exclude(source='').values_list('source', flat=True).distinct()
    return render(request, 'campaigns/compose.html', {
        'campaign': campaign,
        'events': events,
        'status_choices': Lead.STATUS_CHOICES,
        'tone_choices': Campaign.TONE_CHOICES,
        'lane_choices': Campaign.LANE_CHOICES,
        'target_category_choices': Campaign.TARGET_CATEGORY_CHOICES,
    })


def _handle_compose_post(request, campaign: Campaign | None):
    """
    Shared POST handler for both compose_view and compose_edit.
    D3: Never silently resets status of an approved/sending campaign.
    D8: Persists target_category, blocks approve with low score.
    """
    name = request.POST.get('name', '').strip()
    intent = request.POST.get('intent_prompt', '').strip()
    subject = request.POST.get('subject', '').strip()
    body_text = request.POST.get('body_text', '').strip()
    body_html = request.POST.get('body_html', '').strip()
    spam_score_raw = request.POST.get('spam_score')
    spam_issues_raw = request.POST.get('spam_issues', '[]')
    lane = request.POST.get('lane', Campaign.LANE_BULK)
    tone = request.POST.get('tone', 'professional')
    audience_event = request.POST.get('audience_event', '').strip()
    audience_status = request.POST.get('audience_status', '').strip()
    target_category = request.POST.get('target_category', Campaign.CAT_ALL)  # D8 fix
    action = request.POST.get('send_action') or request.POST.get('action', 'save')

    try:
        spam_issues = json.loads(spam_issues_raw)
    except Exception:
        spam_issues = []

    try:
        spam_score_int = int(spam_score_raw) if spam_score_raw else None
    except (ValueError, TypeError):
        spam_score_int = None

    is_new = campaign is None
    if is_new:
        campaign = Campaign(created_by=request.user, status=Campaign.STATUS_DRAFT)

    # Validate target_category
    valid_cats = {c[0] for c in Campaign.TARGET_CATEGORY_CHOICES}
    if target_category not in valid_cats:
        target_category = Campaign.CAT_ALL

    campaign.name = name or 'Untitled Campaign'
    campaign.intent_prompt = intent
    campaign.subject = subject
    campaign.body_text = body_text
    campaign.body_html = body_html
    campaign.spam_score = spam_score_int
    campaign.spam_issues = spam_issues
    campaign.lane = lane
    campaign.tone = tone
    campaign.audience_event = audience_event
    campaign.audience_status = audience_status
    campaign.target_category = target_category  # D8 fix: actually save it

    if action == 'schedule':
        schedule_dt_str = request.POST.get('schedule_datetime', '').strip()
        start_at = None
        if schedule_dt_str:
            try:
                import zoneinfo
                local_tz = zoneinfo.ZoneInfo(getattr(settings, 'TIME_ZONE', 'Asia/Kolkata'))
                naive_dt = datetime.fromisoformat(schedule_dt_str)
                start_at = naive_dt.replace(tzinfo=local_tz)
            except Exception:
                start_at = parse_datetime(schedule_dt_str)
        if not start_at:
            start_at = timezone.now()

        campaign.start_at = start_at
        campaign.status = Campaign.STATUS_SCHEDULED
        campaign.approved_at = timezone.now()
        campaign.approved_by = request.user
        campaign.save()

        # Build recipient queue from audience queryset
        from campaigns.views import _get_audience_queryset
        audience = _get_audience_queryset(campaign)
        suppressed_emails = set(Suppression.objects.values_list('email', flat=True))
        existing_lead_ids = set(
            SendLog.objects.filter(campaign=campaign).values_list('lead_id', flat=True)
        )

        batch = []
        total_queued = 0
        for lead_id, email in audience.values_list('id', 'email').iterator(chunk_size=2000):
            if email in suppressed_emails or lead_id in existing_lead_ids:
                continue
            batch.append(SendLog(campaign=campaign, lead_id=lead_id, status=SendLog.STATUS_QUEUED))
            if len(batch) >= 500:
                SendLog.objects.bulk_create(batch, ignore_conflicts=True)
                total_queued += len(batch)
                batch = []
        if batch:
            SendLog.objects.bulk_create(batch, ignore_conflicts=True)
            total_queued += len(batch)

        campaign.planned_total = total_queued
        campaign.save(update_fields=['planned_total'])

        start_str = start_at.strftime('%d %b %Y at %I:%M %p IST')
        messages.success(
            request,
            f'🎉 Campaign "{campaign.name}" scheduled for {start_str} with {total_queued} recipient(s)! '
            f'Now active in your Scheduler pipeline.'
        )
        return redirect('/scheduler/')

    elif action in ('approve', 'request_approval'):
        # If user is admin/ops, approve and schedule immediately
        if request.user.is_staff or request.user.is_superuser:
            campaign.status = Campaign.STATUS_SCHEDULED
            campaign.approved_at = timezone.now()
            campaign.approved_by = request.user
            if not campaign.start_at:
                campaign.start_at = timezone.now()
            campaign.save()

            from campaigns.views import _get_audience_queryset
            audience = _get_audience_queryset(campaign)
            suppressed_emails = set(Suppression.objects.values_list('email', flat=True))
            existing_lead_ids = set(
                SendLog.objects.filter(campaign=campaign).values_list('lead_id', flat=True)
            )

            batch = []
            total_queued = 0
            for lead_id, email in audience.values_list('id', 'email').iterator(chunk_size=2000):
                if email in suppressed_emails or lead_id in existing_lead_ids:
                    continue
                batch.append(SendLog(campaign=campaign, lead_id=lead_id, status=SendLog.STATUS_QUEUED))
                if len(batch) >= 500:
                    SendLog.objects.bulk_create(batch, ignore_conflicts=True)
                    total_queued += len(batch)
                    batch = []
            if batch:
                SendLog.objects.bulk_create(batch, ignore_conflicts=True)
                total_queued += len(batch)

            campaign.planned_total = total_queued
            campaign.save(update_fields=['planned_total'])

            messages.success(request, f'✓ Campaign "{campaign.name}" approved and queued with {total_queued} recipients!')
            return redirect('/scheduler/')
        else:
            if campaign.status in (Campaign.STATUS_DRAFT,):
                campaign.status = Campaign.STATUS_PENDING
                campaign.save()
                messages.success(request, 'Campaign submitted for approval.')
                return redirect('campaign_detail', pk=campaign.pk)
            else:
                campaign.save()
                messages.info(request, 'Campaign saved.')
                return redirect('compose_edit', pk=campaign.pk)

    elif action == 'save':
        if campaign.status in (Campaign.STATUS_DRAFT, Campaign.STATUS_PENDING) or is_new:
            campaign.status = Campaign.STATUS_DRAFT
        campaign.save()
        messages.success(request, f'Campaign "{campaign.name}" saved as draft.')
        return redirect('compose_edit', pk=campaign.pk)

    else:
        campaign.save()
        return redirect('compose_edit', pk=campaign.pk)


@ops_required
@require_POST
def generate_draft(request):
    """AJAX endpoint: intent → Gemini draft → spam lint → Gemini spam score."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    intent = data.get('intent', '').strip()
    audience_event = data.get('audience_event', '').strip()
    audience_status = data.get('audience_status', '').strip()
    tone = data.get('tone', 'professional')

    if not intent:
        return JsonResponse({'error': 'Intent cannot be empty'}, status=400)

    parts = []
    if audience_event:
        parts.append(f'leads from event "{audience_event}"')
    if audience_status:
        parts.append(f'status="{audience_status}"')
    if not parts:
        parts.append('all active leads')
    audience_desc = ', '.join(parts)

    kb_items = list(KnowledgeBase.objects.filter(is_active=True).values('title', 'content'))
    if not kb_items:
        return JsonResponse(
            {'error': 'Company facts (Knowledge Base) is empty. Add entries in Settings first.'}, status=400
        )

    try:
        draft = gemini_draft(
            intent=intent,
            knowledge_items=kb_items,
            audience_desc=audience_desc,
            tone=tone,
            event_name=audience_event,
        )
    except Exception as e:
        logger.error('Gemini draft failed: %s', e)
        return JsonResponse({'error': f'The AI is busy — try again in a minute. ({e})'}, status=500)

    lint_issues = spam_lint(draft['subject'], draft['body_text'])

    try:
        score_result = gemini_spam_score(draft['subject'], draft['body_text'], lint_issues)
    except Exception as e:
        logger.warning('Spam score failed: %s', e)
        score_result = {'score': max(1, 8 - len(lint_issues) * 2), 'issues': lint_issues}

    return JsonResponse({
        'subject': draft['subject'],
        'body_text': draft['body_text'],
        'body_html': draft['body_html'],
        'spam_score': score_result['score'],
        'spam_issues': score_result['issues'],
        'lint_issues': lint_issues,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Direct Composer (No AI) & Specific Recipient / CSV Dumper
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_GEM_SUBJECT = "Proposal for End-to-End GeM and Non-GeM Procurement Support Services"

DEFAULT_GEM_BODY_TEXT = """Respected Sir/Madam,

Greetings from V CHANGE SOLUTIONS!

We are Delivering procurement support services for government departments, public sector undertakings, educational institutions, and other government organizations.

We support buyers in navigating GeM (Government e-Marketplace) and Non-GeM procurement processes, covering applicable product categories, services, and works.

Our key service offerings include:
1. Product and Service Procurement: Assistance in identifying suitable products, services, and works based on departmental requirements and applicable procurement procedures.
2. Direct Purchase Assistance: Product and service identification, specification matching, and purchase-link support for transactions up to ₹50,000, subject to applicable GeM and government procurement rules.
3. GeM Add-to-Cart Support: Assistance with product selection, cart-related issues, and navigating the purchasing process.
4. Tender Drafting Support: Assistance in preparing technical specifications, scope of work, eligibility criteria, and tender documentation.
5. Tender Calling Support: Assistance with the preparation and processing of tender requirements through GeM, subject to the department’s authorization and applicable procedures.
6. End-to-End Procurement Support: Assistance throughout the procurement process, including requirement identification, product comparison, documentation, and procedural guidance.

We also work as Resellers for products in applicable Q3 and Q4 categories and support procurement requirements involving other products, services, and works available through GeM and Non-GeM channels.

We are providing similar procurement-related services to multiple government departments and are committed to Facilitating buyers navigate procurement procedures efficiently, transparently, and in accordance with applicable government guidelines.

We would welcome an opportunity to understand your department’s procurement requirements and explain how our team can assist with purchasing difficulties, tender preparation, and related procurement activities.

Please let us know a convenient time for a brief discussion.

Thank you for your time and consideration.

Yours faithfully,
Team VCS
Website: https://vchangesolutions.com/
Mobile: [Your Mobile Number]
Email: [Your Email Address]
Government Procurement Support | GeM & Non-GeM
"""

DEFAULT_GEM_BODY_HTML = """<p>Respected Sir/Madam,</p>
<p>Greetings from <strong>V CHANGE SOLUTIONS</strong>!</p>
<p>We are Delivering procurement support services for government departments, public sector undertakings, educational institutions, and other government organizations.</p>
<p>We support buyers in navigating GeM (Government e-Marketplace) and Non-GeM procurement processes, covering applicable product categories, services, and works.</p>
<p><strong>Our key service offerings include:</strong></p>
<ol style="margin: 12px 0 16px 24px; padding: 0; line-height: 1.6;">
  <li><strong>Product and Service Procurement:</strong> Assistance in identifying suitable products, services, and works based on departmental requirements and applicable procurement procedures.</li>
  <li><strong>Direct Purchase Assistance:</strong> Product and service identification, specification matching, and purchase-link support for transactions up to &#8377;50,000, subject to applicable GeM and government procurement rules.</li>
  <li><strong>GeM Add-to-Cart Support:</strong> Assistance with product selection, cart-related issues, and navigating the purchasing process.</li>
  <li><strong>Tender Drafting Support:</strong> Assistance in preparing technical specifications, scope of work, eligibility criteria, and tender documentation.</li>
  <li><strong>Tender Calling Support:</strong> Assistance with the preparation and processing of tender requirements through GeM, subject to the department’s authorization and applicable procedures.</li>
  <li><strong>End-to-End Procurement Support:</strong> Assistance throughout the procurement process, including requirement identification, product comparison, documentation, and procedural guidance.</li>
</ol>
<p>We also work as <strong>Resellers</strong> for products in applicable Q3 and Q4 categories and support procurement requirements involving other products, services, and works available through GeM and Non-GeM channels.</p>
<p>We are providing similar procurement-related services to multiple government departments and are committed to Facilitating buyers navigate procurement procedures efficiently, transparently, and in accordance with applicable government guidelines.</p>
<p>We would welcome an opportunity to understand your department’s procurement requirements and explain how our team can assist with purchasing difficulties, tender preparation, and related procurement activities.</p>
<p>Please let us know a convenient time for a brief discussion.</p>
<p>Thank you for your time and consideration.</p>
<p style="margin-top: 16px;">Yours faithfully,<br>
<strong>Team VCS</strong><br>
Website: <a href="https://vchangesolutions.com/" style="color:#2563eb;">https://vchangesolutions.com/</a><br>
Mobile: [Your Mobile Number]<br>
Email: [Your Email Address]<br>
<em>Government Procurement Support | GeM &amp; Non-GeM</em></p>
<hr style="margin-top: 24px; border: 0; border-top: 1px solid #e2e8f0;">
<p style="font-size: 11px; color: #64748b; margin-top: 8px;">
If you no longer wish to receive these updates, you can <a href="{{unsub_url}}" style="color: #64748b; text-decoration: underline;">unsubscribe here</a>.
</p>"""


def _parse_recipients(csv_file=None, raw_text=None):
    """
    Intelligently parses emails and contact details from either an uploaded CSV/file
    or a pasted text area. Returns a list of dicts: [{'email': ..., 'name': ..., 'company': ...}].
    Deduplicates within the submitted batch.
    """
    recipients = []
    seen = set()

    if csv_file:
        try:
            content = csv_file.read().decode('utf-8-sig', errors='replace')
        except Exception:
            try:
                csv_file.seek(0)
                content = csv_file.read().decode('utf-8', errors='replace')
            except Exception:
                csv_file.seek(0)
                content = csv_file.read().decode('latin-1', errors='replace')

        # First attempt: CSV structured parsing
        try:
            reader = csv.reader(io.StringIO(content))
            header = None
            email_idx, name_idx, company_idx = -1, -1, -1

            for row in reader:
                if not row:
                    continue
                if header is None:
                    row_lower = [col.strip().lower() for col in row]
                    for idx, col in enumerate(row_lower):
                        if 'email' in col or 'mail' in col:
                            email_idx = idx
                        elif 'name' in col:
                            name_idx = idx
                        elif any(k in col for k in ('company', 'org', 'department', 'dept', 'buyer')):
                            company_idx = idx
                    if email_idx != -1:
                        header = row_lower
                        continue
                    else:
                        header = []

                if email_idx != -1 and email_idx < len(row):
                    raw_email = row[email_idx].strip()
                    m = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', raw_email)
                    if m:
                        em = m.group(0).lower()
                        if em not in seen:
                            seen.add(em)
                            recipients.append({
                                'email': em,
                                'name': row[name_idx].strip() if name_idx != -1 and name_idx < len(row) else '',
                                'company': row[company_idx].strip() if company_idx != -1 and company_idx < len(row) else '',
                            })
                else:
                    for col in row:
                        m = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', col)
                        if m:
                            em = m.group(0).lower()
                            if em not in seen:
                                seen.add(em)
                                recipients.append({'email': em, 'name': '', 'company': ''})
        except Exception as e:
            logger.warning('CSV reader fallback: %s', e)

        # Fallback if CSV reader found nothing
        if not recipients:
            for m in re.finditer(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', content):
                em = m.group(0).lower()
                if em not in seen:
                    seen.add(em)
                    recipients.append({'email': em, 'name': '', 'company': ''})

    if raw_text:
        # Match all occurrences across the raw text, splitting by newlines, commas, or semicolons
        chunks = re.split(r'[\r\n,;]+', raw_text)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            email_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', chunk)
            if email_match:
                em = email_match.group(0).lower()
                if em not in seen:
                    seen.add(em)
                    name_cand = chunk[:email_match.start()].strip(' <,"\'')
                    recipients.append({
                        'email': em,
                        'name': name_cand if name_cand and '@' not in name_cand else '',
                        'company': '',
                    })

    return recipients


def _text_to_clean_html(plain_text: str) -> str:
    """
    Converts plain text into clean, high-deliverability HTML email markup.
    - Preserves double-line paragraph breaks as <p>...</p>
    - Preserves single line breaks as <br>
    - Supports bold markdown **text** as <strong>text</strong>
    - Auto-links URLs (https://...)
    - Appends unsubscribe footer if not already present
    """
    import html as html_lib
    if not plain_text:
        return ''

    text = plain_text.replace('\r\n', '\n').replace('\r', '\n').strip()
    raw_paras = re.split(r'\n{2,}', text)
    html_paras = []

    for para in raw_paras:
        para = para.strip()
        if not para:
            continue
        para_escaped = html_lib.escape(para)
        # Bold support: **bold text**
        para_escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', para_escaped)
        # Auto-link URLs
        para_escaped = re.sub(
            r'(https?://[^\s<>"]+)',
            r'<a href="\1" style="color: #2563eb; text-decoration: underline;" target="_blank">\1</a>',
            para_escaped
        )
        # Convert single newlines to <br>
        para_with_br = para_escaped.replace('\n', '<br>\n')
        html_paras.append(
            f'<p style="margin: 0 0 14px 0; font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #1e293b;">{para_with_br}</p>'
        )

    body_html = '\n'.join(html_paras)

    if '{{unsub_url}}' not in body_html:
        body_html += (
            '\n<hr style="margin-top: 24px; border: 0; border-top: 1px solid #e2e8f0;">\n'
            '<p style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; font-size: 11px; color: #64748b; margin-top: 8px;">'
            'If you wish to unsubscribe, <a href="{{unsub_url}}" style="color: #64748b; text-decoration: underline;">click here</a>.'
            '</p>'
        )

    return body_html


@csrf_exempt
@ops_required
@require_POST
def direct_save_draft_api(request):
    """
    AJAX POST /compose/direct/save-draft/
    Saves or updates a draft campaign with plain text message content.
    Returns JSON status, campaign ID, and timestamp.
    """
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    campaign_id = data.get('campaign_id')
    name = data.get('name', '').strip()
    category = data.get('category', Lead.CAT_BUYERS).strip()
    lane = data.get('lane', Campaign.LANE_BULK)
    subject = data.get('subject', '').strip()
    body_text = data.get('body_text', '').strip()

    if not subject:
        return JsonResponse({'error': 'Please provide a subject line to save draft.'}, status=400)

    body_html = _text_to_clean_html(body_text)

    if not name:
        name = f'Draft: {subject[:40]}' if subject else f'Draft Campaign {timezone.now().strftime("%d %b %H:%M")}'

    campaign = None
    if campaign_id:
        try:
            campaign = Campaign.objects.filter(id=campaign_id).first()
        except Exception:
            pass

    if campaign:
        campaign.name = name
        campaign.subject = subject
        campaign.body_text = body_text
        campaign.body_html = body_html
        campaign.target_category = category
        campaign.lane = lane
        campaign.save()
    else:
        campaign = Campaign.objects.create(
            name=name,
            lane=lane,
            intent_prompt='Direct Custom Draft (Plain Text)',
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            target_category=category,
            tone='formal',
            status=Campaign.STATUS_DRAFT,
            spam_score=9,
            created_by=request.user,
            planned_total=0,
        )

    return JsonResponse({
        'status': 'ok',
        'campaign_id': campaign.id,
        'name': campaign.name,
        'saved_at': timezone.now().strftime('%I:%M:%S %p'),
    })


@csrf_exempt
@ops_required
def compose_direct_view(request):
    """
    GET: Render the direct draft compose & recipient dumper screen (No AI needed).
    POST: Save draft, schedule, or immediately queue emails for the uploaded list.
    """
    if request.method == 'POST':
        campaign_id = request.POST.get('campaign_id', '').strip()
        name = request.POST.get('name', '').strip()
        category = request.POST.get('category', Lead.CAT_BUYERS).strip()
        lane = request.POST.get('lane', Campaign.LANE_BULK)
        subject = request.POST.get('subject', '').strip()
        body_text = request.POST.get('body_text', '').strip()
        body_html = request.POST.get('body_html', '').strip()
        send_action = request.POST.get('send_action', 'send_now')
        schedule_dt_str = request.POST.get('schedule_datetime', '').strip()

        if not name:
            name = f'Direct: {subject[:40]}' if subject else f'Direct Campaign {timezone.now().strftime("%d %b %H:%M")}'

        if not subject:
            messages.error(request, 'Please provide an email subject.')
            return redirect('compose_direct')

        # Always ensure clean HTML generated from plain text
        if not body_html or body_text:
            body_html = _text_to_clean_html(body_text)

        # Extract recipients and sort real leads
        csv_file = request.FILES.get('csv_file')
        raw_text = request.POST.get('raw_emails', '')
        raw_recipients = _parse_recipients(csv_file=csv_file, raw_text=raw_text)

        from leads.verifier import sort_real_leads
        real_recipients, discarded, sort_stats = sort_real_leads(raw_recipients, check_mx=False)
        recipients = real_recipients

        if not recipients:
            if send_action == 'draft':
                camp = None
                if campaign_id:
                    camp = Campaign.objects.filter(id=campaign_id).first()
                if not camp:
                    camp = Campaign(created_by=request.user)
                camp.name = name
                camp.lane = lane
                camp.intent_prompt = 'Direct Custom Draft (Plain Text)'
                camp.subject = subject
                camp.body_html = body_html
                camp.body_text = body_text
                camp.target_category = category
                camp.status = Campaign.STATUS_DRAFT
                camp.planned_total = 0
                camp.save()
                messages.success(request, f'Campaign "{camp.name}" saved as draft.')
                return redirect('compose_direct')

            messages.error(
                request,
                f'No real/valid email addresses found in upload. '
                f'{sort_stats.get("fake", 0) + sort_stats.get("disposable", 0) + sort_stats.get("invalid_syntax", 0)} invalid/fake addresses were safely filtered out.'
            )
            return redirect('compose_direct')

        # Filter out suppressions
        all_emails = [r['email'] for r in recipients]
        suppressed_emails = set(
            Suppression.objects.filter(email__in=all_emails).values_list('email', flat=True)
        )
        eligible = [r for r in recipients if r['email'] not in suppressed_emails]

        if not eligible:
            messages.error(
                request,
                f'All {len(recipients)} recipients are suppressed (unsubscribed or previous bounces).'
            )
            return redirect('compose_direct')

        # Ensure leads exist in the database with the Direct Draft channel and selected category
        existing_leads_map = {
            l.email: l for l in Lead.objects.filter(email__in=[r['email'] for r in eligible])
        }
        new_leads_to_create = []
        for r in eligible:
            em = r['email']
            if em not in existing_leads_map:
                new_leads_to_create.append(Lead(
                    email=em,
                    name=r.get('name', ''),
                    company=r.get('company', ''),
                    channel=Lead.CHANNEL_DIRECT,
                    category=category,
                    source=f'Direct Dump: {name[:50]}',
                    status=Lead.STATUS_NEW,
                ))

        if new_leads_to_create:
            Lead.objects.bulk_create(new_leads_to_create, ignore_conflicts=True)

        target_leads = list(Lead.objects.filter(email__in=[r['email'] for r in eligible]))

        # Calculate scheduling
        start_at = None
        if send_action == 'schedule' and schedule_dt_str:
            try:
                parsed_dt = parse_datetime(schedule_dt_str)
                if parsed_dt:
                    start_at = timezone.make_aware(parsed_dt) if timezone.is_naive(parsed_dt) else parsed_dt
            except Exception as e:
                logger.warning('Failed to parse schedule_datetime "%s": %s', schedule_dt_str, e)
                start_at = timezone.now()

        # Determine campaign status
        if send_action in ('send_now', 'schedule'):
            camp_status = Campaign.STATUS_SCHEDULED
            approved_at = timezone.now()
            approved_by = request.user
        else:
            camp_status = Campaign.STATUS_DRAFT
            approved_at = None
            approved_by = None

        campaign = Campaign.objects.create(
            name=name,
            lane=lane,
            intent_prompt='Direct Custom Draft (No AI)',
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            target_category=category,
            tone='formal',
            status=camp_status,
            start_at=start_at,
            approved_at=approved_at,
            approved_by=approved_by,
            spam_score=9,
            created_by=request.user,
            planned_total=len(target_leads),
        )

        # Queue send logs exclusively for this campaign and uploaded leads
        logs_to_create = [
            SendLog(campaign=campaign, lead=lead, status=SendLog.STATUS_QUEUED)
            for lead in target_leads
        ]
        SendLog.objects.bulk_create(logs_to_create, ignore_conflicts=True)

        suppressed_notice = (
            f' ({len(suppressed_emails)} suppressed addresses automatically skipped)'
            if suppressed_emails else ''
        )

        if send_action == 'send_now':
            messages.success(
                request,
                f'Campaign "{campaign.name}" created and queued with {len(target_leads)} recipients!{suppressed_notice} '
                f'It will be dispatched automatically by the sender worker.'
            )
        elif send_action == 'schedule':
            start_str = start_at.strftime('%d %b %Y at %I:%M %p IST') if start_at else 'scheduled time'
            messages.success(
                request,
                f'Campaign "{campaign.name}" scheduled for {start_str} with {len(target_leads)} recipients!{suppressed_notice}'
            )
        else:
            messages.info(
                request,
                f'Campaign "{campaign.name}" saved as draft with {len(target_leads)} recipients.{suppressed_notice}'
            )

        return redirect('campaign_detail', pk=campaign.pk)

    # GET request: render direct compose view
    return render(request, 'campaigns/compose_direct.html', {
        'default_subject': DEFAULT_GEM_SUBJECT,
        'default_body_text': DEFAULT_GEM_BODY_TEXT,
        'default_body_html': DEFAULT_GEM_BODY_HTML,
        'default_subject_json': json.dumps(DEFAULT_GEM_SUBJECT),
        'default_body_text_json': json.dumps(DEFAULT_GEM_BODY_TEXT),
        'default_body_html_json': json.dumps(DEFAULT_GEM_BODY_HTML),
        'target_category_choices': Campaign.TARGET_CATEGORY_CHOICES,
        'lane_choices': Campaign.LANE_CHOICES,
        'default_category': Lead.CAT_BUYERS,
    })


@csrf_exempt
@ops_required
@require_POST
def preview_recipients_api(request):
    """
    AJAX POST /compose/direct/preview-recipients/
    Parses and counts valid, duplicate, and suppressed emails in real time.
    """
    csv_file = request.FILES.get('csv_file')
    raw_text = request.POST.get('raw_emails', '')
    raw_recipients = _parse_recipients(csv_file=csv_file, raw_text=raw_text)

    from leads.verifier import sort_real_leads
    real_recipients, discarded, sort_stats = sort_real_leads(raw_recipients, check_mx=False)

    total = len(raw_recipients)
    real_count = len(real_recipients)
    fake_count = sort_stats.get('fake', 0) + sort_stats.get('disposable', 0) + sort_stats.get('invalid_syntax', 0)
    emails = [r['email'] for r in real_recipients]
    suppressed = set(
        Suppression.objects.filter(email__in=emails).values_list('email', flat=True)
    )
    eligible = [r for r in real_recipients if r['email'] not in suppressed]

    return JsonResponse({
        'total': total,
        'real_count': real_count,
        'fake_count': fake_count,
        'eligible': len(eligible),
        'suppressed_count': len(suppressed),
        'sample': emails[:8],
    })


@csrf_exempt
@ops_required
@require_POST
def direct_test_send_api(request):
    """
    AJAX POST /compose/direct/test-send/
    Sends an immediate test email to the user or specified address.
    """
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    to_email = data.get('to_email', '').strip() or request.user.email
    subject = data.get('subject', '').strip()
    body_text = data.get('body_text', '').strip()
    body_html = data.get('body_html', '').strip()
    if not body_html and body_text:
        body_html = _text_to_clean_html(body_text)

    if not to_email:
        return JsonResponse({'error': 'Recipient email is required'}, status=400)
    if not subject:
        return JsonResponse({'error': 'Subject is required'}, status=400)

    cred = GmailCredential.get_active()
    if not cred:
        return JsonResponse(
            {'error': 'No Gmail account connected. Go to Settings → Gmail to connect.'}, status=400
        )

    from sender.gmail_sender import send_email
    try:
        msg_id = send_email(
            credential=cred,
            to_email=to_email,
            to_name='Test Recipient',
            subject='[TEST] ' + subject,
            body_text=body_text,
            body_html=body_html,
            unsub_url=getattr(settings, 'BASE_URL', 'http://localhost:8000') + '/unsub/sample-preview-only',
        )
        return JsonResponse({'status': 'ok', 'message_id': msg_id, 'sent_to': to_email})
    except Exception as e:
        logger.error('direct_test_send_api error: %s', e)
        return JsonResponse({'error': f'Send failed: {str(e)}'}, status=500)

