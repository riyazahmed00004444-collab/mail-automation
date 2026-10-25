from django.contrib import admin
from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ['email', 'name', 'company', 'source', 'status', 'created_at']
    list_filter = ['status', 'source']
    search_fields = ['email', 'name', 'company']
    list_per_page = 50
    readonly_fields = ['created_at']
