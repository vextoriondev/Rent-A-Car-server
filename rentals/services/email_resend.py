"""Email delivery through Resend (https://resend.com/docs/api-reference/emails/send-email).

The key lives only in the environment. With no key set, `send_email` uses the console adapter, which logs the message and
reports `sent=False`: it never claims a delivery that did not happen (M7 contract, point d).
"""
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from django.conf import settings

log = logging.getLogger(__name__)
RESEND_URL = 'https://api.resend.com/emails'

@dataclass
class EmailResult:
    sent: bool
    provider: str
    message_id: str = ''
    error: str = ''

def resend_configured() -> bool:
    return bool(settings.RESEND_API_KEY and settings.EMAIL_FROM_ADDRESS)

def send_email(to: str, subject: str, text: str, html: str = '', reply_to: str = '') -> EmailResult:
    """Send one email. Never raises on a provider failure: the caller records `error` and keeps the notice retryable."""
    if not to:
        return EmailResult(False, 'none', error='No recipient address.')
    if not resend_configured():
        log.info('Email not sent (Resend not configured): to=%s subject=%s', to, subject)
        return EmailResult(False, 'console', error='Resend is not configured. No email was sent.')
    body = {'from': f'{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>', 'to': [to], 'subject': subject, 'text': text}
    if html: body['html'] = html
    if reply_to: body['reply_to'] = reply_to
    req = urllib.request.Request(RESEND_URL, data=json.dumps(body).encode(), method='POST',
        headers={'Authorization': f'Bearer {settings.RESEND_API_KEY}', 'Content-Type': 'application/json',
                 'User-Agent': 'rentflow/1.0'})   # Resend's firewall rejects urllib's default User-Agent
    try:
        with urllib.request.urlopen(req, timeout=settings.EMAIL_TIMEOUT) as resp:
            data = json.loads(resp.read() or b'{}')
        return EmailResult(True, 'resend', message_id=str(data.get('id', '')))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        return EmailResult(False, 'resend', error=f'Resend rejected the email (HTTP {e.code}): {detail}')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return EmailResult(False, 'resend', error=f'Could not reach Resend: {e}')
