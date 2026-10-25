"""
sender/management/commands/export_for_prod.py
Usage:
  python manage.py export_for_prod --out seed.json

Exports: leads, suppressions, KnowledgeBase, and historical SendLogs
(sent/bounced/failed/skipped only — NOT queued rows which are stale duplicates).
Campaigns are exported as DRAFTS to avoid re-triggering the engine.
"""
import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Export clean data for production seed (run locally, upload to PA)'

    def add_arguments(self, parser):
        parser.add_argument('--out', default='seed.json', help='Output file path')

    def handle(self, *args, **options):
        from leads.models import Lead
        from sender.models import Suppression, SendLog
        from knowledge.models import KnowledgeBase
        from campaigns.models import Campaign

        out_path = Path(options['out'])
        self.stdout.write(f'Exporting to {out_path}…')

        data = {
            'exported_at': timezone.now().isoformat(),
            'leads': [],
            'suppressions': [],
            'knowledge': [],
            'campaigns': [],
            'send_logs_history': [],
        }

        # Leads
        leads = Lead.objects.all().values(
            'id', 'email', 'name', 'company', 'phone', 'source',
            'status', 'category', 'created_at', 'last_contacted_at'
        )
        data['leads'] = list(leads)
        self.stdout.write(f'  Leads: {len(data["leads"]):,}')

        # Suppressions
        sups = Suppression.objects.all().values('email', 'reason', 'notes', 'created_at')
        data['suppressions'] = list(sups)
        self.stdout.write(f'  Suppressions: {len(data["suppressions"])}')

        # KnowledgeBase
        kb = KnowledgeBase.objects.all().values('title', 'content', 'is_active', 'created_at')
        data['knowledge'] = list(kb)
        self.stdout.write(f'  Knowledge: {len(data["knowledge"])}')

        # Campaigns — as drafts (reset status so they won't auto-send on import)
        campaigns = Campaign.objects.all().values(
            'id', 'name', 'lane', 'intent_prompt', 'audience_event', 'audience_status',
            'target_category', 'tone', 'subject', 'body_html', 'body_text',
            'spam_score', 'spam_issues', 'created_at'
        )
        camp_list = []
        for c in campaigns:
            c['status'] = 'draft'  # always import as draft
            camp_list.append(c)
        data['campaigns'] = camp_list
        self.stdout.write(f'  Campaigns: {len(data["campaigns"])} (exported as drafts)')

        # Historical SendLogs (audit trail only — exclude queued)
        history_statuses = [SendLog.STATUS_SENT, SendLog.STATUS_BOUNCED,
                            SendLog.STATUS_FAILED, SendLog.STATUS_SKIPPED]
        logs = SendLog.objects.filter(status__in=history_statuses).values(
            'campaign_id', 'lead_id', 'status', 'gmail_message_id', 'error', 'sent_at', 'created_at'
        )
        data['send_logs_history'] = list(logs)
        self.stdout.write(f'  Historical SendLogs: {len(data["send_logs_history"]):,}')

        # Write file (use str() for datetimes)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)

        size_mb = out_path.stat().st_size / 1024 / 1024
        self.stdout.write(self.style.SUCCESS(
            f'\nExported to {out_path} ({size_mb:.1f} MB)\n'
            f'IMPORTANT: Delete seed.json after uploading to PA. It contains contact data.'
        ))
