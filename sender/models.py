"""
sender/models.py — Core data models for sending, suppression, credentials and settings.
"""
import uuid
from django.db import models
from django.utils import timezone
from campaigns.models import Campaign
from leads.models import Lead


class SendLog(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_SENDING = 'sending'     # claimed by a worker run
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'       # transient failure, may retry
    STATUS_SKIPPED = 'skipped'     # suppressed / frequency-capped / placeholder
    STATUS_BOUNCED = 'bounced'     # permanent delivery failure
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Waiting to send'),
        (STATUS_SENDING, 'Being sent'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, "Couldn't send"),
        (STATUS_SKIPPED, 'Skipped'),
        (STATUS_BOUNCED, "Address doesn't exist"),
    ]

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='send_logs')
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='send_logs')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    gmail_message_id = models.CharField(max_length=255, blank=True)
    # Idempotency: generated before sending; allows crash-resend detection
    message_id_header = models.CharField(max_length=255, blank=True, db_index=True)
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    # Retry / concurrency fields (D7)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('campaign', 'lead')]
        indexes = [
            models.Index(fields=['status', 'next_attempt_at']),
            models.Index(fields=['campaign', 'status']),
        ]

    def __str__(self):
        return f'{self.campaign.name} → {self.lead.email} [{self.status}]'


class Suppression(models.Model):
    REASON_UNSUB = 'unsubscribed'
    REASON_BOUNCED = 'bounced'
    REASON_COMPLAINT = 'complaint'
    REASON_COLD = 'cold'
    REASON_CHOICES = [
        (REASON_UNSUB, 'Unsubscribed'),
        (REASON_BOUNCED, 'Bounced / Invalid'),
        (REASON_COMPLAINT, 'Spam complaint'),
        (REASON_COLD, 'Cold / No interest'),
    ]

    email = models.EmailField(unique=True, db_index=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    created_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.email} [{self.reason}]'


class GmailCredential(models.Model):
    email = models.EmailField(unique=True)
    # Stored encrypted via EncryptedTextField (see migrations for initial encryption)
    access_token = models.TextField()
    refresh_token = models.TextField()
    token_expiry = models.DateTimeField(null=True, blank=True)
    scopes = models.TextField(default='https://www.googleapis.com/auth/gmail.send')
    connected_at = models.DateTimeField(default=timezone.now)
    # Flag set by the auth error classifier; cleared on successful send
    needs_reauth = models.BooleanField(default=False)

    def __str__(self):
        return self.email

    @classmethod
    def get_active(cls):
        return cls.objects.first()


class DomainCheck(models.Model):
    """Cache for DNS MX verification results (TTL 30 days). Fixes D5."""
    domain = models.CharField(max_length=255, unique=True, db_index=True)
    ok = models.BooleanField()
    reason = models.CharField(max_length=255)
    checked_at = models.DateTimeField(default=timezone.now)

    CACHE_DAYS = 30

    class Meta:
        verbose_name = 'Domain MX cache'

    def __str__(self):
        return f'{self.domain} [{"ok" if self.ok else "fail"}]'

    @property
    def is_stale(self):
        from datetime import timedelta
        return timezone.now() > self.checked_at + timedelta(days=self.CACHE_DAYS)


class Setting(models.Model):
    """Singleton settings table."""
    # ── Volume limits ──────────────────────────────────────────────────────────
    daily_cap = models.IntegerField(default=50, help_text='Max emails per calendar day (IST)')
    batch_size = models.IntegerField(default=25, help_text='Rows per engine run (legacy, prefer daily_cap)')
    per_hour_cap = models.IntegerField(null=True, blank=True, help_text='Max per hour (auto = ceil(daily_cap/window_hours))')
    min_delay_seconds = models.IntegerField(default=20, help_text='Min jitter delay between emails (seconds)')
    max_delay_seconds = models.IntegerField(default=60, help_text='Max jitter delay between emails (seconds)')
    weekly_cap_bump = models.FloatField(default=1.20, help_text='Max cap increase factor (1.20 = 20%)')

    # ── Send window ───────────────────────────────────────────────────────────
    window_start = models.TimeField(default='10:00', help_text='Daily send window start (local time)')
    window_end = models.TimeField(default='17:00', help_text='Daily send window end (local time)')
    # Comma-separated weekday numbers 0=Mon … 6=Sun; default Mon-Sat
    send_weekdays = models.CharField(max_length=20, default='0,1,2,3,4,5')

    # ── Frequency cap ─────────────────────────────────────────────────────────
    min_days_between_emails = models.IntegerField(default=7, help_text='Days before same lead can be contacted again')

    # ── Bounce / circuit breaker ──────────────────────────────────────────────
    bounce_pause_percent = models.FloatField(default=5.0, help_text='Auto-pause if hard-bounce rate exceeds this %')
    paused = models.BooleanField(default=True, help_text='Global send pause (new installs start paused)')
    pause_reason = models.TextField(blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)

    # ── Quota / auth block ────────────────────────────────────────────────────
    sending_blocked_until = models.DateTimeField(null=True, blank=True,
                                                  help_text='Set by quota error; clears automatically')

    # ── Low lead alerts ───────────────────────────────────────────────────────
    low_lead_threshold = models.IntegerField(default=50, help_text='Threshold of remaining new leads to trigger daily alert')
    last_low_lead_alert_date = models.DateField(null=True, blank=True, help_text='Date when daily low lead alert was last sent')
    alert_recipient_email = models.EmailField(blank=True, help_text='Custom email for low lead alerts (defaults to sender account)')

    # ── Worker heartbeat ──────────────────────────────────────────────────────
    last_worker_heartbeat = models.DateTimeField(null=True, blank=True)
    last_worker_summary = models.JSONField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Setting'
        verbose_name_plural = 'Settings'

    def save(self, *args, **kwargs):
        self.pk = 1  # Always update the single row
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def allowed_weekdays(self) -> list[int]:
        """Return list of int weekday numbers that sending is allowed on."""
        try:
            return [int(d.strip()) for d in self.send_weekdays.split(',') if d.strip()]
        except ValueError:
            return [0, 1, 2, 3, 4, 5]  # Mon-Sat fallback

    def __str__(self):
        return f'Settings (cap={self.daily_cap}, paused={self.paused})'
