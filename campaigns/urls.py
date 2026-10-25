from django.urls import path
from . import views
from .tracking_view import tracking

urlpatterns = [
    path('', views.campaigns_list, name='campaigns_list'),
    path('<int:pk>/', views.campaign_detail, name='campaign_detail'),
    path('<int:pk>/approve/', views.campaign_approve, name='campaign_approve'),
    path('<int:pk>/pause/', views.campaign_pause, name='campaign_pause'),
    path('<int:pk>/cancel/', views.campaign_cancel, name='campaign_cancel'),
    path('tracking/', tracking, name='tracking'),
]
