from django.db import models
from .organizations import Organization
from .parties import Party

class NotificationOutbox(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    event_key = models.CharField(max_length=120)
    recipient = models.ForeignKey(Party,on_delete=models.PROTECT)
    event_type = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    state = models.CharField(max_length=12,default='queued')
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','event_key','recipient'],name='unique_notice_event_recipient')]

class NotificationDelivery(models.Model):
    """One send attempt target of an outbox row: one channel, to the party or (is_admin_copy) to the admin. The outbox row says what happened in the business; this says what happened to the message.
    `sent` means the provider accepted it. Nothing here claims the person received it, and the console adapter never reaches `sent`."""
    class State(models.TextChoices): QUEUED='queued','Queued'; SENDING='sending','Sending'; SENT='sent','Sent'; FAILED='failed','Failed'; SKIPPED='skipped_no_contact','Skipped, no contact'
    outbox = models.ForeignKey(NotificationOutbox,on_delete=models.CASCADE,related_name='deliveries')
    channel = models.CharField(max_length=8)   # sms | email | none (a party with no usable contact)
    is_admin_copy = models.BooleanField(default=False)
    to_address = models.CharField(max_length=254,blank=True)
    state = models.CharField(max_length=20,choices=State.choices,default=State.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    provider = models.CharField(max_length=20,blank=True)
    provider_message_id = models.CharField(max_length=120,blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['outbox','channel','is_admin_copy'],name='unique_delivery_target')]
        indexes=[models.Index(fields=['state','updated_at'])]
