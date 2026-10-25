from django.contrib import admin
from .models import KnowledgeBase


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ['title', 'is_active', 'updated_at']
    list_filter = ['is_active']
    search_fields = ['title', 'content']
