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
CSRF_TRUSTED_ORIGINS = [h.strip() for h in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS','').split(',') if h.strip()]
INSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','rentals']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','rentals.audit_context.RequestContextMiddleware','rentals.i18n.LanguageMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
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
PRIVATE_MEDIA_ROOT = BASE_DIR / 'private_media'
DOCUMENT_URL_MAX_AGE = int(os.environ.get('DOCUMENT_URL_MAX_AGE','300'))
