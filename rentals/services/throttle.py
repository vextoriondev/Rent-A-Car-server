"""Login brute-force protection: database-backed so it holds across workers.
5 failures for the same username+IP within 15 minutes, or 20 for the same username from anywhere, lock sign-in for 15 minutes.
Every failure and every lockout is logged to the `rentals.security` logger. A successful login clears the username+IP counter."""
import logging
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from ..models import LoginThrottle
from .finance import FinanceError

log = logging.getLogger('rentals.security')
WINDOW = timedelta(minutes=15)
LOCK = timedelta(minutes=15)
LIMIT_PAIR = 5
LIMIT_USER = 20


def client_ip(request):
    return request.META.get('REMOTE_ADDR', '') or 'unknown'


def _keys(username, ip):
    u = (username or '').strip().lower()[:120]
    return [(f'pair:{u}:{ip}', LIMIT_PAIR), (f'user:{u}', LIMIT_USER)]


def check_not_locked(request, username):
    now = timezone.now()
    for key, _ in _keys(username, client_ip(request)):
        row = LoginThrottle.objects.filter(key=key).first()
        if row and row.locked_until and row.locked_until > now:
            wait = int((row.locked_until - now).total_seconds()) + 1
            log.warning('login blocked (locked) username=%s ip=%s retry_in=%ss', username, client_ip(request), wait)
            raise FinanceError('login_locked', f'Too many failed sign-in attempts. Try again in {max(1, wait // 60)} minute(s).', status=429, retry_after=wait)


def record_failure(request, username):
    now = timezone.now()
    ip = client_ip(request)
    with transaction.atomic():
        for key, limit in _keys(username, ip):
            row, _ = LoginThrottle.objects.select_for_update().get_or_create(key=key, defaults={'failures': 0, 'window_started': now})
            if now - row.window_started > WINDOW:
                row.failures, row.window_started, row.locked_until = 0, now, None
            row.failures += 1
            if row.failures >= limit:
                row.locked_until = now + LOCK
                log.warning('login lockout username=%s ip=%s failures=%s', username, ip, row.failures)
            row.save()
    log.info('login failed username=%s ip=%s', username, ip)


def record_success(request, username):
    LoginThrottle.objects.filter(key=_keys(username, client_ip(request))[0][0]).delete()
