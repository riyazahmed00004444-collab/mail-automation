from django.urls import path
from . import scheduler_views

urlpatterns = [
    path('', scheduler_views.scheduler_dashboard, name='scheduler_dashboard'),
    path('update-settings/', scheduler_views.update_scheduler_settings, name='update_scheduler_settings'),
    path('toggle-pause/', scheduler_views.toggle_scheduler_pause, name='toggle_scheduler_pause'),
    path('campaign/<int:pk>/reschedule/', scheduler_views.reschedule_campaign, name='reschedule_campaign'),
    path('campaign/<int:pk>/run-now/', scheduler_views.run_campaign_now, name='run_campaign_now'),
    path('campaign/<int:pk>/pause-toggle/', scheduler_views.toggle_campaign_pause, name='toggle_campaign_pause'),
    path('process-now/', scheduler_views.process_queue_now, name='scheduler_process_now'),
    path('live-dispatch/', scheduler_views.live_dispatch_stream_api, name='live_dispatch_stream_api'),
]
