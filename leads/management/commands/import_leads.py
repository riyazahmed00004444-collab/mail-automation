"""
leads/management/commands/import_leads.py
Usage: python manage.py import_leads <csv_file_path>

Imports a CSV file of leads directly into the database.
Fast bulk insert — handles 26,000+ rows in seconds.
"""
import csv
import time
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from leads.models import Lead
from sender.models import Suppression


DATE_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y')

COL_ALIASES = {
    'email':      ['email'],
    'first_name': ['first name', 'firstname', 'first_name'],
    'last_name':  ['last name', 'lastname', 'last_name'],
    'name':       ['name', 'full name', 'fullname'],
    'company':    ['company name', 'company', 'organisation', 'organization'],
    'phone':      ['phone', 'mobile', 'phone number'],
    'source':     ['username', 'source', 'event', 'membership label'],
    'date':       ['join date', 'started time', 'event_date', 'date'],
}


def _resolve(headers_lower, aliases):
    for a in aliases:
        if a in headers_lower:
            return headers_lower[a]
    return None


def _clean(v):
    return '' if v.strip() in ('-', 'n/a', 'none', '') else v.strip()


def _parse_date(raw):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            pass
    return None


class Command(BaseCommand):
    help = 'Import leads from a CSV file (supports WordPress membership export)'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Path to the CSV file')
        parser.add_argument(
            '--chunk', type=int, default=500,
            help='Bulk insert chunk size (default: 500)'
        )

    def handle(self, *args, **options):
        csv_path = Path(options['csv_path'])
        if not csv_path.exists():
            raise CommandError(f'File not found: {csv_path}')

        self.stdout.write(f'Reading {csv_path.name} ...')
        t0 = time.time()

        # Read file
        try:
            content = csv_path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            content = csv_path.read_text(encoding='latin-1')

        reader = csv.DictReader(content.splitlines())
        if not reader.fieldnames:
            raise CommandError('CSV has no headers.')

        headers_lower = {h.strip().lower(): h.strip() for h in reader.fieldnames}
        if 'email' not in headers_lower:
            raise CommandError('CSV must have an "email" column.')

        col_email   = headers_lower.get('email')
        col_first   = _resolve(headers_lower, COL_ALIASES['first_name'])
        col_last    = _resolve(headers_lower, COL_ALIASES['last_name'])
        col_name    = _resolve(headers_lower, COL_ALIASES['name'])
        col_company = _resolve(headers_lower, COL_ALIASES['company'])
        col_phone   = _resolve(headers_lower, COL_ALIASES['phone'])
        col_source  = _resolve(headers_lower, COL_ALIASES['source'])
        col_date    = _resolve(headers_lower, COL_ALIASES['date'])

        self.stdout.write('Loading existing emails & suppression list ...')
        suppressed_emails = set(Suppression.objects.values_list('email', flat=True))
        existing_emails   = set(Lead.objects.values_list('email', flat=True))

        self.stdout.write('Parsing rows ...')
        to_create   = []
        seen        = set()
        suppressed  = skipped = invalid = 0

        for row in reader:
            row = {k.strip(): (v or '').strip() for k, v in row.items()}
            email = row.get(col_email, '').lower().strip()

            if not email or '@' not in email:
                invalid += 1
                continue
            if email in suppressed_emails:
                suppressed += 1
                continue
            if email in existing_emails or email in seen:
                skipped += 1
                continue

            # Name
            name = row.get(col_name, '') if col_name else ''
            if not name:
                first = row.get(col_first, '') if col_first else ''
                last  = row.get(col_last,  '') if col_last  else ''
                name  = f'{first} {last}'.strip()

            company = _clean(row.get(col_company, '') if col_company else '')
            phone   = _clean(row.get(col_phone,   '') if col_phone   else '')
            source  = row.get(col_source, '') if col_source else ''
            if source.strip() == '-':
                source = ''

            raw_date = row.get(col_date, '') if col_date else ''
            event_date = _parse_date(raw_date) if raw_date and raw_date != '-' else None

            to_create.append(Lead(
                email=email, name=name, company=company, phone=phone,
                source=source, event_date=event_date, status=Lead.STATUS_NEW,
            ))
            seen.add(email)

        self.stdout.write(f'Parsed {len(to_create)} new leads. Inserting in chunks ...')

        chunk_size = options['chunk']
        imported = 0
        with transaction.atomic():
            for i in range(0, len(to_create), chunk_size):
                chunk = to_create[i:i + chunk_size]
                result = Lead.objects.bulk_create(chunk, ignore_conflicts=True)
                imported += len(result)
                self.stdout.write(f'  {min(i + chunk_size, len(to_create))}/{len(to_create)} inserted...')

        elapsed = time.time() - t0
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Done in {elapsed:.1f}s\n'
            f'   Imported  : {imported}\n'
            f'   Skipped   : {skipped} (duplicates)\n'
            f'   Suppressed: {suppressed}\n'
            f'   Invalid   : {invalid}\n'
        ))
