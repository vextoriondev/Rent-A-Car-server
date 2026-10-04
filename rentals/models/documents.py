from django.conf import settings
from django.db import models
from .organizations import Organization
from .parties import Vehicle

class MaintenanceCase(models.Model):
    """A repair/maintenance request. A mechanic can open one; money only moves through the expense engine, which links back here."""
    class Status(models.TextChoices): REQUESTED='requested','Requested'; APPROVED='approved','Approved'; IN_PROGRESS='in_progress','In progress'; DONE='done','Done'; CANCELLED='cancelled','Cancelled'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle,on_delete=models.PROTECT,related_name='maintenance_cases')
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    issue = models.TextField()
    vendor = models.CharField(max_length=180,blank=True)
    quote = models.DecimalField(max_digits=14,decimal_places=2,null=True,blank=True)
    downtime_from = models.DateField(null=True,blank=True)
    downtime_to = models.DateField(null=True,blank=True)
    status = models.CharField(max_length=12,choices=Status.choices,default=Status.REQUESTED)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    approved_at = models.DateTimeField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class Document(models.Model):
    """A private file (receipt, agreement scan, ...) attached to one record. Bytes live in private storage and are only
    reachable through a short-lived signed URL; the row keeps a SHA-256 so tampering is detectable."""
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    target_type = models.CharField(max_length=20)
    target_id = models.CharField(max_length=40)
    storage_key = models.CharField(max_length=200,unique=True)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: indexes=[models.Index(fields=['organization','target_type','target_id'])]

class AuditEvent(models.Model):
    """Append-only: every state change by a service writes one row with who, what, before/after and why. Rows cannot be edited or deleted."""
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE,related_name='audit_events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=60)
    target_type = models.CharField(max_length=30,blank=True)
    target_id = models.CharField(max_length=40,blank=True)
    before = models.JSONField(null=True,blank=True)
    after = models.JSONField(null=True,blank=True)
    reason = models.CharField(max_length=240,blank=True)
    request_id = models.CharField(max_length=40,blank=True)
    ip = models.GenericIPAddressField(null=True,blank=True)
    class Meta:
        indexes=[models.Index(fields=['organization','at']),models.Index(fields=['organization','target_type','target_id']),models.Index(fields=['organization','action'])]
    def save(self,*args,**kwargs):
        if self.pk:raise RuntimeError('Audit events are append-only')
        super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise RuntimeError('Audit events are append-only')
