from django.urls import path
from . import api_views

urlpatterns = [
    path('process-queue', api_views.process_queue_api, name='process_queue_api'),
    path('stats', api_views.live_stats, name='live_stats'),
    path('sync-bounces', api_views.sync_bounces_api, name='sync_bounces_api'),
    path('manual-suppress', api_views.manual_suppress_api, name='manual_suppress_api'),
    # verify-leads removed from API — use: python manage.py verify_leads --limit N
    path('send-test-email', api_views.send_test_email_api, name='send_test_email_api'),
]
