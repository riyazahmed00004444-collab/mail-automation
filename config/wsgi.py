"""
config/wsgi.py — WSGI application entry point.
PythonAnywhere WSGI config should add project to sys.path and set:
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    os.environ['ENV_FILE'] = '/home/<user>/bidalert.env'
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
