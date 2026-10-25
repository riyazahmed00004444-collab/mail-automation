from django.urls import path
from . import views

urlpatterns = [
    path('', views.knowledge_list, name='knowledge_list'),
    path('new/', views.knowledge_create, name='knowledge_create'),
    path('<int:pk>/edit/', views.knowledge_edit, name='knowledge_edit'),
    path('<int:pk>/delete/', views.knowledge_delete, name='knowledge_delete'),
]
