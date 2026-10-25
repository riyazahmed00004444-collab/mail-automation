"""
sender/management/commands/repair_queue.py
Usage:
  python manage.py repair_queue --dry-run   # default: show what would change
  python manage.py repair_queue             # apply fixes

What it does:
  (a) Lists campaigns with queued rows whose status is NOT sending-eligible.
  (b) Cancels (marks skipped) queued rows of non-eligible campaigns.
  (c) For leads queued in more than one eligible campaign, keeps only the
      earliest campaign's row and skips the rest (prevents D2 duplicate sends).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Repair duplicate/stale queue rows (run after D1/D2 fixes)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=True,
                            help='Show what would change without applying (default: True)')
        parser.add_argument('--apply', action='store_true',
                            help='Actually apply changes (overrides --dry-run)')

    def handle(self, *args, **options):
        from sender.models import SendLog
        from campaigns.models import Campaign

        dry_run = not options.get('apply', False)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — add --apply to make changes'))

        # ── Part A: campaigns with queued rows but non-eligible status ─────────
        from django.db.models import Count, Q
        bad_campaigns = Campaign.objects.exclude(
            status__in=list(Campaign.SENDING_ELIGIBLE)
        ).annotate(
            ann_queued=Count('send_logs', filter=Q(send_logs__status='queued'))
        ).filter(ann_queued__gt=0)

        self.stdout.write(f'\n=== Non-eligible campaigns with queued rows ===')
        total_non_eligible = 0
        for c in bad_campaigns:
            self.stdout.write(
                f'  Campaign #{c.pk} "{c.name}" status={c.status} '
                f'queued={c.ann_queued}'
            )
            total_non_eligible += c.ann_queued

        self.stdout.write(f'Total rows to cancel: {total_non_eligible}')

        if not dry_run and total_non_eligible > 0:
            for c in bad_campaigns:
                cancelled = SendLog.objects.filter(
                    campaign=c, status=SendLog.STATUS_QUEUED
                ).update(
                    status=SendLog.STATUS_SKIPPED,
                    error=f'Campaign not eligible for sending (status={c.status})',
                    sent_at=timezone.now(),
                )
                self.stdout.write(f'  Cancelled {cancelled} rows for campaign #{c.pk}')

        # ── Part B: leads queued in more than one eligible campaign ────────────
        self.stdout.write(f'\n=== Leads queued in multiple eligible campaigns ===')
        from django.db.models import Min

        # Find lead IDs that appear in multiple queued rows across eligible campaigns
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT lead_id, COUNT(DISTINCT campaign_id) as camp_count
                FROM sender_sendlog sl
                JOIN campaigns_campaign c ON sl.campaign_id = c.id
                WHERE sl.status = 'queued'
                  AND c.status IN ('scheduled', 'sending')
                GROUP BY lead_id
                HAVING camp_count > 1
            """)
            dup_lead_ids = [row[0] for row in cursor.fetchall()]

        self.stdout.write(f'Leads queued in 2+ campaigns: {len(dup_lead_ids)}')

        dup_rows_to_skip = 0
        for lead_id in dup_lead_ids:
            rows = list(
                SendLog.objects.filter(
                    lead_id=lead_id,
                    status=SendLog.STATUS_QUEUED,
                    campaign__status__in=list(Campaign.SENDING_ELIGIBLE),
                ).order_by('campaign__created_at').values_list('id', flat=True)
            )
            # Keep the first (earliest campaign), skip the rest
            to_skip = rows[1:]
            dup_rows_to_skip += len(to_skip)
            if not dry_run and to_skip:
                SendLog.objects.filter(id__in=to_skip).update(
                    status=SendLog.STATUS_SKIPPED,
                    error='Skipped: duplicate campaign — same lead in an earlier campaign',
                    sent_at=timezone.now(),
                )

        self.stdout.write(f'Duplicate rows to skip: {dup_rows_to_skip}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nNo changes made (dry run). Add --apply to execute.'))
        else:
            self.stdout.write(self.style.SUCCESS('\nRepair complete.'))
