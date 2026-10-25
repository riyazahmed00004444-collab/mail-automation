from django.db import models
from django.utils import timezone


class Lead(models.Model):
    STATUS_NEW          = 'new'
    STATUS_CONTACTED    = 'contacted'
    STATUS_ACTIVE       = 'active'
    STATUS_COLD         = 'cold'
    STATUS_UNSUBSCRIBED = 'unsubscribed'
    STATUS_BOUNCED      = 'bounced'

    STATUS_CHOICES = [
        (STATUS_NEW,          'New'),
        (STATUS_CONTACTED,    'Contacted'),
        (STATUS_ACTIVE,       'Active'),
        (STATUS_COLD,         'Cold'),
        (STATUS_UNSUBSCRIBED, 'Unsubscribed'),
        (STATUS_BOUNCED,      'Bounced'),
    ]

    CAT_BUYERS       = 'buyers'
    CAT_GENERAL      = 'general'
    CAT_PROMOTION    = 'promotion'
    CAT_SERVICE      = 'service'
    CAT_PLANS        = 'plans'
    CAT_SUBSCRIPTION = 'subscription'

    CATEGORY_CHOICES = [
        (CAT_BUYERS,       'Buyers'),
        (CAT_GENERAL,      'General'),
        (CAT_PROMOTION,    'Promotion'),
        (CAT_SERVICE,      'Service'),
        (CAT_PLANS,        'Pricing Plans'),
        (CAT_SUBSCRIPTION, 'Continue Subscription'),
    ]

    CHANNEL_TARGETED = 'targeted'
    CHANNEL_DIRECT   = 'direct_draft'

    CHANNEL_CHOICES = [
        (CHANNEL_TARGETED, 'Targeted Campaign'),
        (CHANNEL_DIRECT,   'Direct Draft Dumper'),
    ]

    email      = models.EmailField(unique=True, db_index=True)
    name       = models.CharField(max_length=255, blank=True)
    company    = models.CharField(max_length=255, blank=True)
    phone      = models.CharField(max_length=30, blank=True)
    channel    = models.CharField(max_length=30, choices=CHANNEL_CHOICES,
                                   default=CHANNEL_TARGETED, db_index=True)
    category   = models.CharField(max_length=30, choices=CATEGORY_CHOICES,
                                   default=CAT_GENERAL, db_index=True)
    source     = models.CharField(max_length=255, blank=True,
                                   help_text='Event name e.g. TiE Con 2024')
    event_date = models.DateField(null=True, blank=True)
    interest = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name or self.email} ({self.company or "—"})'
