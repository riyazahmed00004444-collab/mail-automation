from django.urls import path
from . import gmail_views

urlpatterns = [
    path('connect/', gmail_views.gmail_connect, name='gmail_connect'),
    path('callback/', gmail_views.gmail_callback, name='gmail_callback'),
    path('disconnect/', gmail_views.gmail_disconnect, name='gmail_disconnect'),
]
