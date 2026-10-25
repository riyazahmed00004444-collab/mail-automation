from django.urls import path
from . import settings_view

urlpatterns = [
    path('', settings_view.settings_view, name='settings'),
    path('toggle-pause/', settings_view.toggle_pause, name='toggle_pause'),
    path('update-cap/', settings_view.update_cap, name='update_cap'),
]
