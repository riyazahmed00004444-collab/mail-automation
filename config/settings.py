import os
import hmac
from pathlib import Path
import environ
import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    FORCE_HTTPS=(bool, False),
)

# ── Env file loading ──────────────────────────────────────────────────────────
# On PythonAnywhere: set ENV_FILE=/home/<user>/bidalert.env in WSGI config
_env_file = os.environ.get('ENV_FILE', str(BASE_DIR / '.env'))
if os.path.isfile(_env_file):
    environ.Env.read_env(_env_file)

# ── Core settings ─────────────────────────────────────────────────────────────
DEBUG = env('DEBUG', default=False)

_secret_key = env('SECRET_KEY', default='')
if not _secret_key:
    if not DEBUG:
        raise ImproperlyConfigured(
            "SECRET_KEY must be set in production. "
            "Run: python scripts/gen_secrets.py"
        )
    _secret_key = 'insecure-dev-key-do-not-use-in-production'

SECRET_KEY = _secret_key

# Old keys for rotating without breaking signed unsubscribe URLs
_fb_raw = env('SECRET_KEY_FALLBACKS', default='')
SECRET_KEY_FALLBACKS = [k.strip() for k in _fb_raw.split(',') if k.strip()]

# ── Hosts & Origins ───────────────────────────────────────────────────────────
BASE_URL = env('BASE_URL', default='http://localhost:8000').rstrip('/')

_allowed = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
# Derive host from BASE_URL automatically
try:
    from urllib.parse import urlparse as _urlparse
    _parsed_host = _urlparse(BASE_URL).netloc.split(':')[0]
    if _parsed_host and _parsed_host not in _allowed:
        _allowed.append(_parsed_host)
except Exception:
    pass

ALLOWED_HOSTS = _allowed

CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'https://*.pythonanywhere.com',
]
if BASE_URL.startswith('https://') or BASE_URL.startswith('http://'):
    if BASE_URL not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(BASE_URL)

for _origin in env.list('CSRF_EXTRA_ORIGINS', default=[]):
    if _origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_origin)

CSRF_COOKIE_AGE = 7 * 24 * 60 * 60  # 7 days

# ── Installed apps ────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # project apps
    'accounts',
    'leads',
    'campaigns',
    'aiops',
    'sender',
    'knowledge',
]

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'config.middleware.ForceCsrfCookieMiddleware',  # D14: ensure CSRF cookie always set
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = env('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')
DATABASES = {
    'default': dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=240,   # < 5 min PA MySQL idle timeout
    )
}

# SQLite tuning: WAL mode + 30s timeout
if DATABASE_URL.startswith('sqlite'):
    DATABASES['default'].setdefault('OPTIONS', {})
    DATABASES['default']['OPTIONS']['timeout'] = 30

# PostgreSQL / Neon DB tuning (enforce SSL required by Neon)
if 'postgres' in DATABASE_URL:
    DATABASES['default'].setdefault('OPTIONS', {})
    DATABASES['default']['OPTIONS'].setdefault('sslmode', 'require')

# MySQL charset
if 'mysql' in DATABASE_URL:
    DATABASES['default'].setdefault('OPTIONS', {})
    DATABASES['default']['OPTIONS']['charset'] = 'utf8mb4'

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# ── Time ──────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = env('DEFAULT_SEND_TIMEZONE', default='Asia/Kolkata')
USE_I18N = True
USE_TZ = True

# ── Static ────────────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Uploads ───────────────────────────────────────────────────────────────────
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024   # 20 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024   # 20 MB

# ── Google OAuth ──────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = env('GOOGLE_CLIENT_ID', default='')
GOOGLE_CLIENT_SECRET = env('GOOGLE_CLIENT_SECRET', default='')
GOOGLE_OAUTH_REDIRECT_URI = BASE_URL + '/gmail/callback/'

# ── Gemini AI ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY = env('GEMINI_API_KEY', default='')
# Default to a current Flash model — verify the name in Google AI Studio
GEMINI_MODEL = env('GEMINI_MODEL', default='gemini-1.5-flash-latest')

# ── Cron security ─────────────────────────────────────────────────────────────
CRON_SECRET = env('CRON_SECRET', default='')
if not DEBUG and (not CRON_SECRET or CRON_SECRET == 'dev-cron-secret'):
    raise ImproperlyConfigured(
        "CRON_SECRET must be set to a long random value in production. "
        "Run: python scripts/gen_secrets.py"
    )

# ── Signed unsubscribe tokens ─────────────────────────────────────────────────
UNSUB_SIGNING_SALT = 'bidalert-unsub-v1'

# ── Email presentation ────────────────────────────────────────────────────────
SENDER_DISPLAY_NAME = env('SENDER_DISPLAY_NAME', default='BidAlert')
REPLY_TO_EMAIL = env('REPLY_TO_EMAIL', default='')

# ── Security (HTTPS) ──────────────────────────────────────────────────────────
FORCE_HTTPS = env('FORCE_HTTPS', default=False)

if DEBUG:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
else:
    # Use PA's "Force HTTPS" Web tab toggle; only enable redirect if you know
    # X-Forwarded-Proto arrives correctly (otherwise risk redirect loops).
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = FORCE_HTTPS
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 300          # start small; raise to 31536000 after testing
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False  # enable after confirming subdomains work
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

# ── Logging ───────────────────────────────────────────────────────────────────
_log_dir = Path.home() / 'logs'
try:
    _log_dir.mkdir(exist_ok=True)
except Exception:
    _log_dir = BASE_DIR / 'logs'
    _log_dir.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} [{levelname}] {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(_log_dir / 'bidalert.log'),
            'maxBytes': 5 * 1024 * 1024,  # 5 MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {'handlers': ['console', 'file'], 'level': 'WARNING', 'propagate': False},
        'sender': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
        'campaigns': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
        'leads': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
    },
}

# ── Cache (for login throttle) ────────────────────────────────────────────────
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}
