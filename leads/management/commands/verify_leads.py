"""
leads/management/commands/verify_leads.py
Usage: python manage.py verify_leads [--batch 500] [--queued-only]

Checks email formatting and verifies DNS MX records for leads in the database.
Suppresses dead domains and malformed addresses before sending, preserving daily send quota.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from leads.models import Lead
from sender.models import SendLog, Suppression
from leads.verifier import verify_email


class Command(BaseCommand):
    help = 'Verify lead email format and DNS MX records, suppressing dead domains.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch',
            type=int,
            default=200,
            help='Number of leads to verify in this run (default: 200)'
        )
        parser.add_argument(
            '--queued-only',
            action='store_true',
            default=True,
            help='Only verify leads that currently have queued send logs (default: True)'
        )
        parser.add_argument(
            '--all-new',
            action='store_true',
            default=False,
            help='Verify all leads with status=new'
        )

    def handle(self, *args, **options):
        batch_size = options['batch']
        all_new = options['all_new']

        if all_new:
            leads = Lead.objects.filter(status=Lead.STATUS_NEW)[:batch_size]
            self.stdout.write(f"Selecting up to {batch_size} leads with status=NEW...")
        else:
            # Leads that are queued in SendLog
            queued_lead_ids = SendLog.objects.filter(
                status=SendLog.STATUS_QUEUED
            ).values_list('lead_id', flat=True)[:batch_size]
            leads = Lead.objects.filter(id__in=list(queued_lead_ids))
            self.stdout.write(f"Selecting up to {batch_size} QUEUED leads for verification...")

        leads = list(leads)
        total = len(leads)
        if not total:
            self.stdout.write(self.style.SUCCESS("No leads found matching criteria."))
            return

        self.stdout.write(f"Verifying {total} leads...")

        valid_count = 0
        invalid_count = 0

        for idx, lead in enumerate(leads, start=1):
            is_valid, reason = verify_email(lead.email)
            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1
                self.stdout.write(self.style.WARNING(f"  [INVALID] {lead.email} -> {reason}"))

                # Mark lead as bounced
                lead.status = Lead.STATUS_BOUNCED
                lead.save(update_fields=['status'])

                # Update any pending SendLog
                SendLog.objects.filter(
                    lead=lead, status=SendLog.STATUS_QUEUED
                ).update(
                    status=SendLog.STATUS_SKIPPED,
                    error=f'Pre-send check rejected: {reason}',
                    sent_at=timezone.now()
                )

                # Add to suppression
                Suppression.objects.get_or_create(
                    email=lead.email,
                    defaults={
                        'reason': Suppression.REASON_BOUNCED,
                        'notes': f'Pre-send verification: {reason}',
                    }
                )

            if idx % 50 == 0 or idx == total:
                self.stdout.write(f"  Progress: {idx}/{total} checked...")

        self.stdout.write(self.style.SUCCESS(
            f"\nVerification finished:\n"
            f"  - Total Checked: {total}\n"
            f"  - Valid (Deliverable): {valid_count}\n"
            f"  - Invalid / Dead domains suppressed: {invalid_count}\n"
        ))
