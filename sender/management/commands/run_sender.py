"""
sender/management/commands/run_sender.py
Usage:
  python manage.py run_sender                    # one time-boxed run (PA hourly task)
  python manage.py run_sender --max-seconds 3000 # explicit time box
  python manage.py run_sender --loop --interval 60  # continuous (PA always-on task)
  python manage.py run_sender --dry-run          # report what would be sent, don't send
"""
import signal
import sys
import time
import logging

from django.core.management.base import BaseCommand
from django.db import close_old_connections

logger = logging.getLogger(__name__)
_stop = False


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True
    logger.info('SIGTERM received — stopping after current batch')


class Command(BaseCommand):
    help = 'Run the email sending engine (one shot or continuous loop for always-on task)'

    def add_arguments(self, parser):
        parser.add_argument('--max-seconds', type=int, default=3000,
                            help='Time box per run in seconds (default: 3000)')
        parser.add_argument('--loop', action='store_true',
                            help='Run continuously (for always-on task). Exits on SIGTERM.')
        parser.add_argument('--interval', type=int, default=60,
                            help='Seconds between loop iterations (default: 60)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Simulate sending — report what would be sent without actually sending')

    def handle(self, *args, **options):
        from sender.engine import run_once

        max_seconds = options['max_seconds']
        loop = options['loop']
        interval = options['interval']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no emails will be sent'))

        signal.signal(signal.SIGTERM, _handle_sigterm)

        def run_iteration():
            close_old_connections()
            try:
                result = run_once(max_seconds=max_seconds, dry_run=dry_run)
                self._print_summary(result, dry_run)
                return result
            except KeyboardInterrupt:
                self.stdout.write('\nStopped by user.')
                sys.exit(0)
            except Exception as e:
                logger.error('Engine error: %s', e, exc_info=True)
                self.stderr.write(f'Engine error: {e}')
                return {}

        if loop:
            self.stdout.write(f'Starting continuous loop (interval: {interval}s)')
            while not _stop:
                run_iteration()
                if not _stop:
                    self.stdout.write(f'Sleeping {interval}s…')
                    time.sleep(interval)
            self.stdout.write('Loop stopped cleanly.')
        else:
            run_iteration()

    def _print_summary(self, result: dict, dry_run: bool):
        prefix = '[DRY RUN] ' if dry_run else ''
        blocked = result.get('blocked_reason', '')

        if blocked:
            self.stdout.write(self.style.WARNING(f'{prefix}Blocked: {blocked}'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'{prefix}sent={result.get("sent", 0)} '
                f'skipped={result.get("skipped", 0)} '
                f'retried={result.get("retried", 0)} '
                f'failed={result.get("failed", 0)} '
                f'remaining={result.get("remaining", "?")}'
            ))
            if result.get('eta_days'):
                self.stdout.write(f'  ETA: ~{result["eta_days"]} more day(s) at this pace')
