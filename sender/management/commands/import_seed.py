"""
sender/management/commands/import_seed.py
Usage:
  python manage.py import_seed seed.json

Idempotent: skips existing emails/entries. Chunked bulk_create.
Run on PythonAnywhere after uploading seed.json.
DELETE seed.json after import.
"""
import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime
from django.utils import timezone

logger = logging.getLogger(__name__)
CHUNK = 500


class Command(BaseCommand):
    help = 'Import seed data on PythonAnywhere (idempotent, chunked)'

    def add_arguments(self, parser):
        parser.add_argument('seed_file', help='Path to seed.json')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show counts without inserting')

    def handle(self, *args, **options):
        seed_path = Path(options['seed_file'])
        dry_run = options['dry_run']

        if not seed_path.exists():
            raise CommandError(f'File not found: {seed_path}')

        self.stdout.write(f'Reading {seed_path}…')
        with open(seed_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.stdout.write(f'Exported at: {data.get("exported_at", "?")}')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing will be inserted'))

        # ── Leads ──────────────────────────────────────────────────────────────
        from leads.models import Lead
        existing_emails = set(Lead.objects.values_list('email', flat=True))
        new_leads = []
        for row in data.get('leads', []):
            email = row.get('email', '').strip()
            if not email or email in existing_emails:
                continue
            existing_emails.add(email)
            new_leads.append(Lead(
                email=email,
                name=row.get('name', ''),
                company=row.get('company', ''),
                phone=row.get('phone', ''),
                source=row.get('source', ''),
                status=row.get('status', 'new'),
                category=row.get('category', 'general'),
                created_at=parse_datetime(str(row.get('created_at', ''))) or timezone.now(),
            ))
        self.stdout.write(f'Leads: {len(new_leads):,} new (of {len(data["leads"]):,})')
        if not dry_run:
            for i in range(0, len(new_leads), CHUNK):
                Lead.objects.bulk_create(new_leads[i:i + CHUNK], ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(f'  Inserted {len(new_leads):,} leads'))

        # ── Suppressions ───────────────────────────────────────────────────────
        from sender.models import Suppression
        existing_sup = set(Suppression.objects.values_list('email', flat=True))
        new_sups = []
        for row in data.get('suppressions', []):
            email = row.get('email', '').strip()
            if not email or email in existing_sup:
                continue
            existing_sup.add(email)
            new_sups.append(Suppression(
                email=email,
                reason=row.get('reason', 'bounced'),
                notes=row.get('notes', ''),
                created_at=parse_datetime(str(row.get('created_at', ''))) or timezone.now(),
            ))
        self.stdout.write(f'Suppressions: {len(new_sups)} new')
        if not dry_run:
            for i in range(0, len(new_sups), CHUNK):
                Suppression.objects.bulk_create(new_sups[i:i + CHUNK], ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(f'  Inserted {len(new_sups)} suppressions'))

        # ── KnowledgeBase ──────────────────────────────────────────────────────
        from knowledge.models import KnowledgeBase
        existing_kb = set(KnowledgeBase.objects.values_list('title', flat=True))
        new_kb = []
        for row in data.get('knowledge', []):
            title = row.get('title', '').strip()
            if not title or title in existing_kb:
                continue
            existing_kb.add(title)
            new_kb.append(KnowledgeBase(
                title=title,
                content=row['content'],
                is_active=row.get('is_active', True),
            ))
        self.stdout.write(f'KnowledgeBase: {len(new_kb)} new entries')
        if not dry_run:
            KnowledgeBase.objects.bulk_create(new_kb, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(f'  Inserted {len(new_kb)} KB entries'))

        # ── Campaigns (as drafts) ──────────────────────────────────────────────
        from campaigns.models import Campaign
        existing_camp_names = set(Campaign.objects.values_list('name', flat=True))
        new_camps = []
        for row in data.get('campaigns', []):
            name = row.get('name', '').strip()
            if not name or name in existing_camp_names:
                continue
            existing_camp_names.add(name)
            new_camps.append(Campaign(
                name=name,
                lane=row.get('lane', 'bulk'),
                intent_prompt=row.get('intent_prompt', ''),
                audience_event=row.get('audience_event', ''),
                audience_status=row.get('audience_status', ''),
                target_category=row.get('target_category', 'all'),
                tone=row.get('tone', 'professional'),
                subject=row.get('subject', ''),
                body_html=row.get('body_html', ''),
                body_text=row.get('body_text', ''),
                spam_score=row.get('spam_score'),
                spam_issues=row.get('spam_issues', []),
                status='draft',  # Always import as draft
            ))
        self.stdout.write(f'Campaigns: {len(new_camps)} new (as drafts)')
        if not dry_run:
            Campaign.objects.bulk_create(new_camps, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(f'  Inserted {len(new_camps)} campaigns'))

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run complete — nothing was inserted.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                '\nImport complete!\n'
                'IMPORTANT: Delete seed.json now: rm seed.json'
            ))
