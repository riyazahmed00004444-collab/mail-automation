"""
sender/management/commands/backup_db.py
Usage:
  python manage.py backup_db

Creates a timestamped backup in ~/backups/. Keeps last 14 backups.
For SQLite: copies the file. For MySQL: runs mysqldump.
"""
import os
import shutil
import subprocess
import logging
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)
KEEP_LAST = 14


class Command(BaseCommand):
    help = 'Backup the database to ~/backups/ (14-backup rotation)'

    def handle(self, *args, **options):
        backup_dir = Path.home() / 'backups'
        backup_dir.mkdir(exist_ok=True)

        db = settings.DATABASES['default']
        engine = db.get('ENGINE', '')
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')

        if 'sqlite3' in engine:
            src = db.get('NAME', '')
            if not src or not Path(src).exists():
                self.stderr.write('SQLite file not found.')
                return
            dest = backup_dir / f'db_{ts}.sqlite3'
            shutil.copy2(src, dest)
            self.stdout.write(self.style.SUCCESS(f'SQLite backup → {dest}'))

        elif 'mysql' in engine:
            name = db.get('NAME', '')
            user = db.get('USER', '')
            password = db.get('PASSWORD', '')
            host = db.get('HOST', 'localhost')
            port = db.get('PORT', '3306')
            dest = backup_dir / f'db_{ts}.sql'
            env = {**os.environ, 'MYSQL_PWD': password}
            cmd = ['mysqldump', f'-u{user}', f'-h{host}', f'-P{port}', name]
            with open(dest, 'wb') as f:
                result = subprocess.run(cmd, stdout=f, env=env, capture_output=False)
            if result.returncode != 0:
                self.stderr.write(f'mysqldump failed (exit {result.returncode})')
                return
            self.stdout.write(self.style.SUCCESS(f'MySQL backup → {dest}'))

        else:
            self.stderr.write(f'Unsupported database engine: {engine}')
            return

        # Rotation: keep last N backups
        all_backups = sorted(backup_dir.glob('db_*.*'))
        if len(all_backups) > KEEP_LAST:
            for old in all_backups[:-KEEP_LAST]:
                old.unlink()
                self.stdout.write(f'Removed old backup: {old.name}')
