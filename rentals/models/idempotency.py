from django.db import models
from .organizations import Organization

class IdempotencyRecord(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    action = models.CharField(max_length=40)
    key = models.CharField(max_length=120)
    payload_hash = models.CharField(max_length=64)
    object_id = models.PositiveBigIntegerField()
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','action','key'],name='unique_idempotency_key')]
