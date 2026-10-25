from django.urls import path
from . import dashboard_view

urlpatterns = [
    path('', dashboard_view.dashboard, name='dashboard'),
]
