from django.contrib import admin
from .models import SendLog, Suppression, GmailCredential, Setting


@admin.register(SendLog)
class SendLogAdmin(admin.ModelAdmin):
    list_display = ['campaign', 'lead', 'status', 'sent_at']
    list_filter = ['status']
    search_fields = ['lead__email', 'campaign__name']
    readonly_fields = ['created_at', 'sent_at']


@admin.register(Suppression)
class SuppressionAdmin(admin.ModelAdmin):
    list_display = ['email', 'reason', 'created_at']
    list_filter = ['reason']
    search_fields = ['email']


@admin.register(GmailCredential)
class GmailCredentialAdmin(admin.ModelAdmin):
    list_display = ['email', 'connected_at', 'token_expiry']
    readonly_fields = ['connected_at']


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ['daily_cap', 'batch_size', 'paused', 'updated_at']
