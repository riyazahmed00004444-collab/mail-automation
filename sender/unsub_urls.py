from django.urls import path
from . import unsub_views

urlpatterns = [
    path('<str:token>/', unsub_views.unsubscribe_view, name='unsubscribe'),
]
