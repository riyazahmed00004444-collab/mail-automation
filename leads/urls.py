from django.urls import path
from . import views

urlpatterns = [
    path('', views.leads_list, name='leads_list'),
    path('upload/', views.leads_upload, name='leads_upload'),
    path('<int:pk>/', views.lead_detail, name='lead_detail'),
]
