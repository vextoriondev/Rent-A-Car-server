"""Environment-first settings. SQLite is only a local/test fallback."""
import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs
BASE_DIR = Path(__file__).resolve().parent.parent
def _load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
for _env_file in ('.env', '.env.local'):
    _load_env_file(BASE_DIR / _env_file)
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'development-only-change-me')
DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'
if not DEBUG and SECRET_KEY == 'development-only-change-me':
    raise RuntimeError('Set DJANGO_SECRET_KEY for production')
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS','localhost,127.0.0.1').split(',') if h.strip()]
ALLOWED_HOSTS += ['rent-a-car-server-production-fc17.up.railway.app', 'healthcheck.railway.app', '.railway.app', '.up.railway.app', '.vercel.app']
if os.environ.get('RAILWAY_ENVIRONMENT'):   # Railway: its own health check calls from this host, and it names the public domain
    ALLOWED_HOSTS += ([os.environ['RAILWAY_PUBLIC_DOMAIN']] if os.environ.get('RAILWAY_PUBLIC_DOMAIN') else [])
ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS))

CSRF_TRUSTED_ORIGINS = [h.strip() for h in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS','').split(',') if h.strip()]
CSRF_TRUSTED_ORIGINS += [
    'https://rent-a-car-server-production-fc17.up.railway.app',
    'https://rentflow-one-ashy.vercel.app',
    'https://*.vercel.app',
    'https://*.up.railway.app'
]
if DEBUG:
    for _p in range(3000, 3020):
        CSRF_TRUSTED_ORIGINS.extend([f'http://localhost:{_p}', f'http://127.0.0.1:{_p}'])
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(CSRF_TRUSTED_ORIGINS))

INSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','rentals']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','rentals.audit_context.RequestContextMiddleware','rentals.i18n.LanguageMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.debug','django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
if os.environ.get('USE_SQLITE') == '1':
    DATABASES = {'default':{'ENGINE':'django.db.backends.sqlite3','NAME':BASE_DIR/'db.sqlite3'}}
elif os.environ.get('DATABASE_URL'):
    _u = urlparse(os.environ['DATABASE_URL'])
    DATABASES = {'default':{'ENGINE':'django.db.backends.postgresql','NAME':_u.path.lstrip('/'),'USER':_u.username,'PASSWORD':_u.password,'HOST':_u.hostname,'PORT':_u.port or 5432,'CONN_MAX_AGE':60,'OPTIONS':{k:v[0] for k,v in parse_qs(_u.query).items()}}}
else:
    DATABASES = {'default':{'ENGINE':'django.db.backends.postgresql','NAME':os.environ.get('POSTGRES_DB','rentflow'),'USER':os.environ.get('POSTGRES_USER','rentflow'),'PASSWORD':os.environ.get('POSTGRES_PASSWORD',''),'HOST':os.environ.get('POSTGRES_HOST','localhost'),'PORT':os.environ.get('POSTGRES_PORT','5432'),'CONN_MAX_AGE':60}}
AUTH_PASSWORD_VALIDATORS = [{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dhaka'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'   # `collectstatic` target; WhiteNoise serves it (the admin's CSS and scripts)
STORAGES = {'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'whitenoise.storage.CompressedStaticFilesStorage'}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https')
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL','redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND','redis://localhost:6379/1')

import sys
if 'test' in sys.argv: PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']  # tests only: fast hashing

# Private document storage: never served directly; only through signed, user-bound, short-lived URLs (rentals.services.documents).
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024   # request bodies (base64 document uploads are capped at 5 MB of file data)
PRIVATE_MEDIA_ROOT = Path(os.environ.get('PRIVATE_MEDIA_ROOT') or BASE_DIR / 'private_media')   # on Railway: a mounted volume, so uploads survive a redeploy
DOCUMENT_URL_MAX_AGE = int(os.environ.get('DOCUMENT_URL_MAX_AGE','300'))

# Email via Resend (rentals/services/email_resend.py). No key = console adapter: nothing is sent and nothing is reported delivered.
RESEND_API_KEY = os.environ.get('RESEND_API_KEY','')
EMAIL_FROM_ADDRESS = os.environ.get('EMAIL_FROM_ADDRESS','')   # a verified domain in Resend; for testing only, onboarding@resend.dev (delivers just to your own Resend account email)
EMAIL_FROM_NAME = os.environ.get('EMAIL_FROM_NAME','RentFlow')
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT','10'))

# How queued SMS/email notices are sent (rentals/services/notify.py): thread (default, no broker needed) | celery | inline | off. Tests send inline.
NOTICE_DELIVERY = os.environ.get('NOTICE_DELIVERY') or ('inline' if 'test' in sys.argv else 'thread')
if 'test' in sys.argv: RESEND_API_KEY = ''   # a test run must never send a real email, whatever .env.local holds


# ── Files & media (rentals/services/files) ──────────────────────────────────────────────────────────────────────────────
# Bytes never go into PostgreSQL. FILE_STORAGE_BACKEND=s3 (Cloudflare R2, AWS S3, any S3-compatible service) is the production
# setting; `filesystem` writes under PRIVATE_MEDIA_ROOT (a mounted volume) and is meant for development / a single server.
FILE_STORAGE_BACKEND = os.environ.get('FILE_STORAGE_BACKEND') or ('s3' if os.environ.get('S3_BUCKET') else 'filesystem')
S3_BUCKET = os.environ.get('S3_BUCKET', '')
S3_ENDPOINT_URL = os.environ.get('S3_ENDPOINT_URL', '')            # R2: https://<account>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID = os.environ.get('S3_ACCESS_KEY_ID', '')
S3_SECRET_ACCESS_KEY = os.environ.get('S3_SECRET_ACCESS_KEY', '')
S3_REGION = os.environ.get('S3_REGION', 'auto')
S3_KEY_PREFIX = os.environ.get('S3_KEY_PREFIX', 'tasin')
FILE_SIGNED_URL_TTL = int(os.environ.get('FILE_SIGNED_URL_TTL', '300'))   # seconds a signed download link stays valid
# Upload limits (checked again on the server; the browser only mirrors them). Megabytes unless stated.
ATTACH_MAX_IMAGE_MB = int(os.environ.get('ATTACH_MAX_IMAGE_MB', '15'))
ATTACH_MAX_PDF_MB = int(os.environ.get('ATTACH_MAX_PDF_MB', '10'))
ATTACH_MAX_DOC_MB = int(os.environ.get('ATTACH_MAX_DOC_MB', '10'))
ATTACH_MAX_FILES = int(os.environ.get('ATTACH_MAX_FILES', '10'))
ATTACH_MAX_TOTAL_MB = int(os.environ.get('ATTACH_MAX_TOTAL_MB', '40'))
ATTACH_MAX_PIXELS = int(os.environ.get('ATTACH_MAX_PIXELS', str(60_000_000)))   # decompression-bomb guard
# Image optimization. Photos are stored only as WebP variants; scans of documents also keep the original and use a higher quality.
ATTACH_PHOTO_QUALITY = int(os.environ.get('ATTACH_PHOTO_QUALITY', '78'))
ATTACH_DOC_QUALITY = int(os.environ.get('ATTACH_DOC_QUALITY', '90'))
ATTACH_VARIANT_SIZES = {'thumb': 240, 'medium': 900, 'large': 1600}      # longest side in pixels, never upscaled
ATTACH_DOC_LARGE_SIZE = int(os.environ.get('ATTACH_DOC_LARGE_SIZE', '2200'))
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024    # bigger uploads stream to a temp file instead of RAM
