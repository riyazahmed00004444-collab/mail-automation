"""
sender/management/commands/clear_queue.py
Usage:
  python manage.py clear_queue              # Clears queued rows, resets active campaigns
  python manage.py clear_queue --clean-leads # Also removes un-sent leads, strictly preserving history
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Clear queued send logs and reset automation while strictly preserving sent/bounced/failed history'

    def add_arguments(self, parser):
        parser.add_argument('--clean-leads', action='store_true',
                            help='Also remove leads that have no sent/bounced/failed history')

    def handle(self, *args, **options):
        from sender.models import SendLog, Setting
        from leads.models import Lead
        from campaigns.models import Campaign

        clean_leads = options.get('clean_leads', False)

        with transaction.atomic():
            q_count = SendLog.objects.filter(status=SendLog.STATUS_QUEUED).count()
            SendLog.objects.filter(status=SendLog.STATUS_QUEUED).delete()

            c_count = Campaign.objects.filter(
                status__in=[Campaign.STATUS_SCHEDULED, Campaign.STATUS_SENDING]
            ).update(
                status=Campaign.STATUS_DRAFT,
                planned_total=0,
                start_at=None
            )

            leads_deleted = 0
            if clean_leads:
                hist_lead_ids = set(
                    SendLog.objects.filter(
                        status__in=[SendLog.STATUS_SENT, SendLog.STATUS_BOUNCED, SendLog.STATUS_FAILED]
                    ).values_list('lead_id', flat=True)
                )
                leads_deleted = Lead.objects.exclude(id__in=hist_lead_ids).delete()[0]

            s = Setting.get()
            s.last_worker_summary = ''
            s.save()

        self.stdout.write(self.style.SUCCESS(
            f'Queue cleared successfully!\n'
            f'  - Queued logs deleted: {q_count}\n'
            f'  - Active campaigns reset to draft: {c_count}\n'
            f'  - Un-sent leads removed: {leads_deleted if clean_leads else "Skipped (kept in DB)"}\n'
            f'  - Historical records preserved: {SendLog.objects.filter(status=SendLog.STATUS_SENT).count()} sent, '
            f'{SendLog.objects.filter(status=SendLog.STATUS_BOUNCED).count()} bounced, '
            f'{SendLog.objects.filter(status=SendLog.STATUS_FAILED).count()} failed.'
        ))
