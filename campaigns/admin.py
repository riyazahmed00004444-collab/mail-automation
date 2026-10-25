from django.contrib import admin
from .models import Campaign


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'lane', 'status', 'spam_score', 'created_by', 'created_at']
    list_filter = ['status', 'lane']
    search_fields = ['name', 'subject']
    readonly_fields = ['created_at', 'approved_at']
