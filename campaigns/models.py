"""
campaigns/models.py — Campaign model with full lifecycle states and scheduler fields.
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Campaign(models.Model):
    LANE_BULK = 'bulk'
    LANE_INDIVIDUAL = 'individual'
    LANE_CHOICES = [
        (LANE_BULK, 'Bulk Campaign'),
        (LANE_INDIVIDUAL, 'Individual / Special'),
    ]

    # ── Status lifecycle ──────────────────────────────────────────────────────
    # draft → pending_approval → scheduled → sending → done
    #                         ↘ paused ↗ (can pause/resume scheduled or sending)
    #                         → cancelled (terminal)
    STATUS_DRAFT = 'draft'
    STATUS_PENDING = 'pending_approval'
    STATUS_SCHEDULED = 'scheduled'     # approved; waiting for start_at
    STATUS_SENDING = 'sending'         # engine actively processing rows
    STATUS_PAUSED = 'paused'           # manually paused; rows untouched
    STATUS_DONE = 'done'               # all rows processed
    STATUS_CANCELLED = 'cancelled'     # terminal; rows cancelled
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PENDING, 'Pending Approval'),
        (STATUS_SCHEDULED, 'Scheduled'),
        (STATUS_SENDING, 'Sending'),
        (STATUS_PAUSED, 'Paused'),
        (STATUS_DONE, 'Done'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    # Statuses where the engine is allowed to send rows
    SENDING_ELIGIBLE = {STATUS_SCHEDULED, STATUS_SENDING}
    # Statuses visible in the "active" list on Home
    ACTIVE_STATUSES = {STATUS_SCHEDULED, STATUS_SENDING, STATUS_PAUSED}

    TONE_CHOICES = [
        ('professional', 'Professional'),
        ('friendly', 'Friendly'),
        ('urgent', 'Urgent'),
        ('formal', 'Formal'),
        ('casual', 'Casual'),
    ]

    CAT_ALL          = 'all'
    CAT_BUYERS       = 'buyers'
    CAT_GENERAL      = 'general'
    CAT_PROMOTION    = 'promotion'
    CAT_SERVICE      = 'service'
    CAT_PLANS        = 'plans'
    CAT_SUBSCRIPTION = 'subscription'

    TARGET_CATEGORY_CHOICES = [
        (CAT_ALL,          'All Leads'),
        (CAT_BUYERS,       'Buyers'),
        (CAT_GENERAL,      'General'),
        (CAT_PROMOTION,    'Promotion'),
        (CAT_SERVICE,      'Service'),
        (CAT_PLANS,        'Pricing Plans'),
        (CAT_SUBSCRIPTION, 'Continue Subscription'),
    ]

    # ── Basic fields ──────────────────────────────────────────────────────────
    name            = models.CharField(max_length=255)
    lane            = models.CharField(max_length=20, choices=LANE_CHOICES, default=LANE_BULK)
    intent_prompt   = models.TextField(help_text='Plain English: what this email should do')
    audience_event  = models.CharField(max_length=255, blank=True, help_text='Filter by event/source name')
    audience_status = models.CharField(max_length=20, blank=True, help_text='Filter by lead status')
    target_category = models.CharField(
        max_length=30, choices=TARGET_CATEGORY_CHOICES,
        default=CAT_ALL, db_index=True,
        help_text='Target leads in this category only'
    )
    tone            = models.CharField(max_length=20, choices=TONE_CHOICES, default='professional')
    subject         = models.CharField(max_length=255, blank=True)
    body_html       = models.TextField(blank=True)
    body_text       = models.TextField(blank=True)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    spam_score      = models.IntegerField(null=True, blank=True, help_text='1-10, need ≥7 to approve')
    spam_issues     = models.JSONField(default=list, blank=True)

    # ── Scheduler fields (Phase 2) ────────────────────────────────────────────
    start_at        = models.DateTimeField(null=True, blank=True,
                                           help_text='When to start sending. Null = immediately when inside window.')
    per_day_limit   = models.IntegerField(null=True, blank=True,
                                           help_text='Campaign-level daily limit. Null = use global Setting.')
    use_global_window = models.BooleanField(default=True,
                                             help_text='Use global send window; if False, use campaign window fields')
    window_start    = models.TimeField(null=True, blank=True, help_text='Campaign-specific window start (if use_global_window=False)')
    window_end      = models.TimeField(null=True, blank=True, help_text='Campaign-specific window end (if use_global_window=False)')
    send_weekdays   = models.CharField(max_length=20, blank=True,
                                        help_text='Campaign-specific weekdays (if use_global_window=False), e.g. "0,1,2,3,4,5"')
    planned_total   = models.IntegerField(null=True, blank=True, help_text='Total rows when queue was built')
    paused_reason   = models.TextField(blank=True)
    finished_at     = models.DateTimeField(null=True, blank=True)

    # ── Test tracking ─────────────────────────────────────────────────────────
    tested_at       = models.DateTimeField(null=True, blank=True, help_text='When a test email was last sent')
    tested_to       = models.EmailField(blank=True, help_text='Address the test was sent to')

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_by      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='campaigns_created')
    approved_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='campaigns_approved')
    created_at      = models.DateTimeField(default=timezone.now)
    approved_at     = models.DateTimeField(null=True, blank=True)
    scheduled_at    = models.DateTimeField(null=True, blank=True,
                                            help_text='Deprecated field kept for backwards compatibility; use start_at instead')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} [{self.get_status_display()}]'

    # ── Business logic helpers ────────────────────────────────────────────────
    @property
    def can_approve(self) -> bool:
        return (
            self.status == self.STATUS_PENDING
            and self.spam_score is not None
            and self.spam_score >= 7
        )

    @property
    def is_sending_eligible(self) -> bool:
        """True if the engine should process this campaign's queued rows."""
        return self.status in self.SENDING_ELIGIBLE

    @property
    def recipient_count(self) -> int:
        return self.send_logs.count()

    @property
    def sent_count(self) -> int:
        return self.send_logs.filter(status='sent').count()

    @property
    def queued_count(self) -> int:
        return self.send_logs.filter(status='queued').count()

    def effective_per_day_limit(self, global_cap: int) -> int:
        """Return the effective daily limit for this campaign."""
        return self.per_day_limit if self.per_day_limit is not None else global_cap
