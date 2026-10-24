"""Celery entry points for NOTICE_DELIVERY=celery. The sending logic lives in services/notify.py."""
from celery import shared_task
from .services import notify

@shared_task(bind=True,max_retries=3)
def deliver_notice(self,delivery_id:int):
    return notify.deliver(delivery_id)

@shared_task
def sweep_notices():
    return notify.sweep()
