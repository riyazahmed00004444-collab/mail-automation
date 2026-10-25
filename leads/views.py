"""
leads/views.py — Lead list, upload (CSV paste or file), detail.
"""
import csv
import io
import logging
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q

from .models import Lead
from .verifier import sort_real_leads
from sender.models import Suppression

import re

logger = logging.getLogger(__name__)

# Columns we care about from the WordPress membership export
EXPECTED_HEADERS = ['email', 'name', 'company', 'source', 'event_date']

# Column name aliases — maps CSV header variants → our internal key
COL_ALIASES = {
    'email': [
        'email', 'e-mail', 'email address', 'e-mail address',
        'email 1 - value', 'e-mail 1 - value', 'email 1', 'e-mail 1',
        'mail', 'mail address', 'contact email', 'primary email',
        'work email', 'personal email', 'lead email', 'electronic mail',
    ],
    'first_name': ['first name', 'firstname', 'first_name', 'given name'],
    'last_name':  ['last name', 'lastname', 'last_name', 'family name', 'surname'],
    'name':       ['name', 'full name', 'fullname', 'contact name', 'display name'],
    'company':    ['company name', 'company', 'organisation', 'organization', 'organization 1 - name', 'business', 'work'],
    'phone':      ['phone', 'mobile', 'phone number', 'contact number', 'mobile phone', 'cell', 'phone 1 - value', 'primary phone'],
    'source':     ['username', 'source', 'event', 'membership label', 'membership slug', 'tag', 'tags', 'group membership'],
    'event_date': ['join date', 'started time', 'event_date', 'date', 'created_at', 'created at'],
}


def _resolve_col(headers_lower: dict, aliases: list[str], exclude: list[str] | None = None) -> str | None:
    """Return first alias that matches a CSV header (exact or safe substring), or None."""
    exclude = exclude or []
    # 1. Exact match (highest priority)
    for alias in aliases:
        if alias in headers_lower:
            orig = headers_lower[alias]
            if not any(ex in alias for ex in exclude):
                return orig

    # 2. Safe substring match (only for specific keywords >= 5 chars, avoiding collisions)
    for alias in aliases:
        if len(alias) < 5:
            continue
        for h_key, orig in headers_lower.items():
            if any(ex in h_key for ex in exclude):
                continue
            if alias in h_key:
                return orig
    return None


def _parse_raw_text_fallback(text: str) -> tuple[list[dict], list[str]]:
    """Fallback extractor when file is unstructured or plain email list."""
    rows = []
    found_emails = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+', text)
    seen = set()
    for em in found_emails:
        clean_em = em.strip().lower()
        if clean_em not in seen:
            seen.add(clean_em)
            rows.append({
                'email': clean_em,
                'name': '',
                'company': '',
                'phone': '',
                'source': '',
                'event_date': None,
            })
    if not rows:
        return [], ['No email addresses found in file or text.']
    return rows, []


def _parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """
    Parse CSV text or raw email lists.
    Supports:
      - Standard CSV with header row (including any alias like 'Email', 'E-mail 1 - Value', 'Email Address')
      - Google Contacts / Outlook / phone contacts export formats
      - Substring / fuzzy header detection
      - Content-based column auto-detection (finds which column has emails if header is unknown)
      - Headerless CSVs (where row 1 is already data)
      - Raw single-column email dump or unstructured text
    """
    if not text or not text.strip():
        return [], ['Empty CSV data provided']

    cleaned_text = text.strip()
    rows = []
    errors = []

    try:
        raw_lines = list(csv.reader(io.StringIO(cleaned_text)))
    except Exception:
        raw_lines = []

    if not raw_lines:
        return _parse_raw_text_fallback(cleaned_text)

    # Check if first row looks like a header or if it already contains an email
    first_row = [c.strip() for c in raw_lines[0]] if raw_lines else []
    has_email_in_first_row = any('@' in c for c in first_row)

    # If first row already has an email address, treat as headerless data
    if has_email_in_first_row:
        email_col_idx = 0
        for idx, val in enumerate(first_row):
            if '@' in val:
                email_col_idx = idx
                break
        
        for i, row_vals in enumerate(raw_lines, start=1):
            if not row_vals or email_col_idx >= len(row_vals):
                continue
            email = row_vals[email_col_idx].strip()
            if not email or '@' not in email:
                continue
            name = row_vals[1].strip() if len(row_vals) > 1 and email_col_idx != 1 else ''
            if not name and email_col_idx != 0 and len(row_vals) > 0:
                name = row_vals[0].strip()
            company = row_vals[2].strip() if len(row_vals) > 2 and email_col_idx != 2 else ''
            phone = row_vals[3].strip() if len(row_vals) > 3 and email_col_idx != 3 else ''
            rows.append({
                'email': email,
                'name': name,
                'company': company,
                'phone': phone,
                'source': '',
                'event_date': None,
            })
        return rows, errors

    # Header-based CSV parsing
    try:
        dict_reader = csv.DictReader(io.StringIO(cleaned_text))
        if not dict_reader.fieldnames:
            return _parse_raw_text_fallback(cleaned_text)

        headers_lower = {h.strip().lower(): h for h in dict_reader.fieldnames if h}
        col_email = _resolve_col(headers_lower, COL_ALIASES['email'])

        all_dict_rows = list(dict_reader)

        # Fallback: check if any column header contains 'mail'
        if not col_email:
            for h_lower, orig in headers_lower.items():
                if 'mail' in h_lower:
                    col_email = orig
                    break

        # Fallback: scan first rows to auto-detect which column contains emails
        if not col_email and all_dict_rows:
            col_scores = {}
            for r in all_dict_rows[:10]:
                for k, v in r.items():
                    if v and '@' in v:
                        col_scores[k] = col_scores.get(k, 0) + 1
            if col_scores:
                col_email = max(col_scores.items(), key=lambda x: x[1])[0]

        if not col_email:
            return _parse_raw_text_fallback(cleaned_text)

        col_first      = _resolve_col(headers_lower, COL_ALIASES['first_name'])
        col_last       = _resolve_col(headers_lower, COL_ALIASES['last_name'])
        col_name       = _resolve_col(headers_lower, COL_ALIASES['name'], exclude=['first', 'last', 'given', 'family', 'company', 'org'])
        col_company    = _resolve_col(headers_lower, COL_ALIASES['company'])
        col_phone      = _resolve_col(headers_lower, COL_ALIASES['phone'])
        col_source     = _resolve_col(headers_lower, COL_ALIASES['source'])
        col_event_date = _resolve_col(headers_lower, COL_ALIASES['event_date'])

        for i, row in enumerate(all_dict_rows, start=2):
            row = {k.strip(): (v or '').strip() for k, v in row.items() if k}
            email = row.get(col_email, '').strip()
            if not email or '@' not in email:
                continue

            name = ''
            if col_first or col_last:
                first = row.get(col_first, '') if col_first else ''
                last  = row.get(col_last,  '') if col_last  else ''
                name  = f'{first} {last}'.strip()
            if not name and col_name:
                name = row.get(col_name, '')

            company = row.get(col_company, '') if col_company else ''
            phone   = row.get(col_phone,   '') if col_phone   else ''
            source  = row.get(col_source,  '') if col_source  else ''

            def _clean(v): return '' if v.strip() in ('-', 'n/a', 'none') else v.strip()
            company = _clean(company)
            phone   = _clean(phone)
            if source == '-':
                source = ''

            event_date = None
            raw_date = row.get(col_event_date, '') if col_event_date else ''
            if raw_date and raw_date != '-':
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y'):
                    try:
                        event_date = datetime.strptime(raw_date, fmt).date()
                        break
                    except ValueError:
                        pass

            rows.append({
                'email':      email,
                'name':       name,
                'company':    company,
                'phone':      phone,
                'source':     source,
                'event_date': event_date,
            })

        if not rows:
            return _parse_raw_text_fallback(cleaned_text)

        return rows, errors

    except Exception as e:
        logger.warning('CSV dict parsing error: %s', e)
        return _parse_raw_text_fallback(cleaned_text)


@login_required
def leads_list(request):
    qs = Lead.objects.all()
    q = request.GET.get('q', '')
    status_filter = request.GET.get('status', '')
    event_filter = request.GET.get('event', '')
    channel_filter = request.GET.get('channel', '')

    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(name__icontains=q) | Q(company__icontains=q))
    if status_filter:
        qs = qs.filter(status=status_filter)
    if event_filter:
        qs = qs.filter(source__icontains=event_filter)
    if channel_filter:
        qs = qs.filter(channel=channel_filter)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))

    events = Lead.objects.exclude(source='').values_list('source', flat=True).distinct()

    # Pool statistics (Targeted vs Direct Draft Dumper)
    total_count = Lead.objects.count()
    targeted_total = Lead.objects.filter(channel=Lead.CHANNEL_TARGETED).count()
    targeted_new = Lead.objects.filter(channel=Lead.CHANNEL_TARGETED, status=Lead.STATUS_NEW).count()
    targeted_contacted = Lead.objects.filter(channel=Lead.CHANNEL_TARGETED, status=Lead.STATUS_CONTACTED).count()

    direct_total = Lead.objects.filter(channel=Lead.CHANNEL_DIRECT).count()
    direct_new = Lead.objects.filter(channel=Lead.CHANNEL_DIRECT, status=Lead.STATUS_NEW).count()
    direct_contacted = Lead.objects.filter(channel=Lead.CHANNEL_DIRECT, status=Lead.STATUS_CONTACTED).count()

    return render(request, 'leads/list.html', {
        'page_obj': page,
        'q': q,
        'status_filter': status_filter,
        'event_filter': event_filter,
        'channel_filter': channel_filter,
        'status_choices': Lead.STATUS_CHOICES,
        'channel_choices': Lead.CHANNEL_CHOICES,
        'events': events,
        'total': qs.count(),
        'total_count': total_count,
        'targeted_total': targeted_total,
        'targeted_new': targeted_new,
        'targeted_contacted': targeted_contacted,
        'direct_total': direct_total,
        'direct_new': direct_new,
        'direct_contacted': direct_contacted,
    })


@login_required
def leads_upload(request):
    if request.method == 'POST':
        csv_text = request.POST.get('csv_text', '').strip()
        channel = request.POST.get('channel', Lead.CHANNEL_TARGETED).strip()
        category = request.POST.get('category', Lead.CAT_GENERAL).strip()

        if not csv_text and 'csv_file' in request.FILES:
            f = request.FILES['csv_file']
            try:
                csv_text = f.read().decode('utf-8-sig', errors='replace')
            except Exception:
                try:
                    f.seek(0)
                    csv_text = f.read().decode('utf-8', errors='replace')
                except Exception:
                    f.seek(0)
                    csv_text = f.read().decode('latin-1', errors='replace')

        if not csv_text:
            messages.error(request, 'Please choose a CSV file or paste lead data.')
            return render(request, 'leads/upload.html', {
                'channel_choices': Lead.CHANNEL_CHOICES,
                'category_choices': Lead.CATEGORY_CHOICES,
            })

        raw_rows, parse_errors = _parse_csv(csv_text)
        if not raw_rows:
            err_msg = parse_errors[0] if parse_errors else 'No email addresses could be detected in the file.'
            messages.error(request, f'Upload error: {err_msg}')
            return render(request, 'leads/upload.html', {
                'channel_choices': Lead.CHANNEL_CHOICES,
                'category_choices': Lead.CATEGORY_CHOICES,
            })

        # ── Step 1: Automatic Real Lead Sorter & Filter ───────────────────────
        real_rows, discarded_rows, stats = sort_real_leads(raw_rows, check_mx=False)

        # ── Step 2: Filter Suppressions & DB Duplicates ───────────────────────
        suppressed_emails = set(
            Suppression.objects.values_list('email', flat=True)
        )
        existing_emails = set(
            Lead.objects.values_list('email', flat=True)
        )

        suppressed_count = 0
        existing_dupe_count = 0
        to_create = []
        seen_in_batch = set()

        for row in real_rows:
            email = row['email']
            if email in suppressed_emails:
                suppressed_count += 1
                discarded_rows.append({
                    'email': email,
                    'reason': 'Suppressed address (unsubscribed or prior bounce)',
                    'category': 'suppressed'
                })
                continue

            if email in existing_emails or email in seen_in_batch:
                existing_dupe_count += 1
                discarded_rows.append({
                    'email': email,
                    'reason': 'Already exists in database',
                    'category': 'duplicate'
                })
                continue

            source_label = row.get('source') or ('Direct Draft Dumper' if channel == Lead.CHANNEL_DIRECT else 'Targeted CSV Dumper')
            to_create.append(Lead(
                email=email,
                name=row.get('name', ''),
                company=row.get('company', ''),
                phone=row.get('phone', ''),
                channel=channel,
                category=category,
                source=source_label,
                event_date=row.get('event_date'),
                status=Lead.STATUS_NEW,
            ))
            seen_in_batch.add(email)

        # ── Step 3: Bulk insert genuine leads ─────────────────────────────────
        CHUNK = 500
        imported = 0
        for i in range(0, len(to_create), CHUNK):
            chunk = to_create[i:i + CHUNK]
            created = Lead.objects.bulk_create(chunk, ignore_conflicts=True)
            imported += len(created)

        channel_display = dict(Lead.CHANNEL_CHOICES).get(channel, channel)
        sort_results = {
            'total_parsed': stats['total'],
            'real_imported': imported,
            'fake_dummy_filtered': stats['fake'],
            'disposable_filtered': stats['disposable'],
            'invalid_syntax': stats['invalid_syntax'],
            'mx_failed': stats['mx_failed'],
            'in_file_dupes': stats['duplicates'],
            'db_dupes_skipped': existing_dupe_count,
            'suppressed_skipped': suppressed_count,
            'channel_name': channel_display,
            'discarded_sample': discarded_rows[:20],
            'discarded_total': len(discarded_rows),
        }

        messages.success(
            request,
            f'🎉 Real Leads Sorted! {imported} genuine emails imported into "{channel_display}". '
            f'{len(discarded_rows)} invalid/fake/duplicate emails safely filtered out.'
        )

        return render(request, 'leads/upload.html', {
            'sort_results': sort_results,
            'channel_choices': Lead.CHANNEL_CHOICES,
            'category_choices': Lead.CATEGORY_CHOICES,
            'selected_channel': channel,
            'selected_category': category,
        })

    return render(request, 'leads/upload.html', {
        'channel_choices': Lead.CHANNEL_CHOICES,
        'category_choices': Lead.CATEGORY_CHOICES,
        'sample_csv': '"User ID",Email,Username,"First Name","Last Name","Join Date",Phone,"company name"\n145001,john@acme.com,acme-referral,John,Doe,"2026-09-24 09:00:00",+919876543210,"Acme Corp"',
    })


@login_required
def lead_detail(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    logs = lead.send_logs.select_related('campaign').order_by('-created_at')[:20]
    return render(request, 'leads/detail.html', {'lead': lead, 'logs': logs})
