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
