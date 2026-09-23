"""Outbox worker boundary; configure a real provider before enabling delivery."""
from celery import shared_task
from django.db import transaction
from .models import NotificationOutbox

@shared_task(bind=True,max_retries=3)
def deliver_notice(self,notice_id:int):
    with transaction.atomic():
        notice=NotificationOutbox.objects.select_for_update().get(pk=notice_id)
        if notice.state=='delivered':return 'already delivered'
        notice.attempts+=1
        notice.state='failed'
        notice.last_error='No SMS, email or WhatsApp provider is configured. No message was sent.'
        notice.save(update_fields=['attempts','state','last_error'])
    return 'provider not configured'
