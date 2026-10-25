from django.urls import path
from django.contrib.auth import views as auth_views
from . import views as acct_views
from . import setup_view

urlpatterns = [
    path('login/', acct_views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('setup/', setup_view.setup_wizard, name='setup_wizard'),
]
