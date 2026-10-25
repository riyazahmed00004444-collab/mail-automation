from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('gmail/', include('accounts.gmail_urls')),
    path('leads/', include('leads.urls')),
    path('campaigns/', include('campaigns.urls')),
    path('compose/', include('campaigns.compose_urls')),
    path('scheduler/', include('sender.scheduler_urls')),
    path('knowledge/', include('knowledge.urls')),
    path('api/', include('sender.api_urls')),
    path('logs/', include('sender.urls')),
    path('unsub/', include('sender.unsub_urls')),
    path('settings/', include('accounts.settings_urls')),
    path('', include('accounts.dashboard_urls')),
]
