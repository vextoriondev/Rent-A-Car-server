from django.core.management.base import BaseCommand, CommandError
from rentals.services.email_resend import resend_configured, send_email

class Command(BaseCommand):
    help = 'Send one test email through Resend to check the key and sender address.'
    def add_arguments(self, parser): parser.add_argument('to')
    def handle(self, *args, **opts):
        if not resend_configured(): raise CommandError('Set RESEND_API_KEY and EMAIL_FROM_ADDRESS first.')
        r = send_email(opts['to'], 'RentFlow test email', 'If you can read this, Resend email works.')
        if not r.sent: raise CommandError(r.error)
        self.stdout.write(self.style.SUCCESS(f'Accepted by Resend, message id {r.message_id}'))
