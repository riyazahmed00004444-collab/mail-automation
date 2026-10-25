from django.db import models
from django.utils import timezone


class KnowledgeBase(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField(help_text='Facts about BidAlert services, plans, pricing — AI ONLY uses this')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']
        verbose_name = 'Knowledge Base Entry'
        verbose_name_plural = 'Knowledge Base'

    def __str__(self):
        return self.title
