from django.urls import path
from . import log_views

urlpatterns = [
    path('', log_views.logs_list, name='logs_list'),
]
