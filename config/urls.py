from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse

def health_check(request):
    return JsonResponse({'status': 'ok', 'service': 'bidalert-comms'})

urlpatterns = [
    path('health/', health_check, name='health_check'),
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
