"""
campaigns/migrations/0004_migrate_approved_to_scheduled.py
Data migration: rename 'approved' status to 'scheduled' in all campaign rows.
Also sets paused=True for the global Setting singleton on fresh installs
(production safety: new instances start paused until owner presses "Start").
"""
from django.db import migrations


def migrate_approved_status(apps, schema_editor):
    Campaign = apps.get_model('campaigns', 'Campaign')
    # 'approved' → 'scheduled' (just started / ready to go)
    Campaign.objects.filter(status='approved').update(status='scheduled')
    # 'sending' campaigns stay 'sending' (they are actively being processed)


def migrate_setting_defaults(apps, schema_editor):
    """Ensure the Setting singleton has safe production defaults."""
    Setting = apps.get_model('sender', 'Setting')
    obj, created = Setting.objects.get_or_create(pk=1)
    if created:
        # New install: start paused, safe defaults
        obj.daily_cap = 50
        obj.paused = True
        obj.pause_reason = 'New installation — press "Start sending" in Settings when ready'
        obj.save()


def reverse_migrate(apps, schema_editor):
    Campaign = apps.get_model('campaigns', 'Campaign')
    Campaign.objects.filter(status='scheduled').update(status='approved')


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0003_phase1_scheduler_fields'),
        ('sender', '0002_phase1_scheduler_fields'),
    ]

    operations = [
        migrations.RunPython(migrate_approved_status, reverse_migrate),
        migrations.RunPython(migrate_setting_defaults, migrations.RunPython.noop),
    ]
