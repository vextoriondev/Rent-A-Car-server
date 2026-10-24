from django.conf import settings
from django.db import models
from .organizations import Organization
from .agreements import Agreement, Period

class OrgSettings(models.Model):
    """Per-organization month-end policy. Defaults follow the plan: calendar month-end, admin approves, owner-owes-operator does not block close."""
    organization = models.OneToOneField(Organization,on_delete=models.CASCADE,related_name='settings')
    cutoff_day = models.PositiveSmallIntegerField(default=31,help_text='Informational cutoff day of month; 31 = calendar month-end')
    approval_role = models.CharField(max_length=16,default='admin',help_text='Role allowed to approve a settlement (admin can always approve)')
    negative_owner_blocks_close = models.BooleanField(default=False)
    admin_copy_email = models.EmailField(blank=True,help_text='Every money notice is also sent here (M7)')
    admin_copy_phone = models.CharField(max_length=32,blank=True,help_text='Every money notice is also sent here by SMS (M7)')

class Settlement(models.Model):
    """The month-end workflow around one Period: draft -> pending_approval -> approved -> closed (-> reopened -> ...)."""
    class State(models.TextChoices): DRAFT='draft','Draft'; PENDING='pending_approval','Pending approval'; APPROVED='approved','Approved'; CLOSED='closed','Closed'; REOPENED='reopened','Reopened'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    agreement = models.ForeignKey(Agreement,on_delete=models.PROTECT,related_name='settlements')
    period = models.OneToOneField(Period,on_delete=models.PROTECT,related_name='settlement')
    month = models.DateField()
    state = models.CharField(max_length=20,choices=State.choices,default=State.DRAFT)
    version = models.PositiveIntegerField(default=1,help_text='Incremented on every reopen; keeps notification event keys unique per closing')
    statement_hash = models.CharField(max_length=64,blank=True,help_text='Fingerprint of the statement when approval was requested')
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+',null=True,blank=True)
    prepared_at = models.DateTimeField(null=True,blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+',null=True,blank=True)
    approved_at = models.DateTimeField(null=True,blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+',null=True,blank=True)
    closed_at = models.DateTimeField(null=True,blank=True)
    reopened_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+',null=True,blank=True)
    reopened_at = models.DateTimeField(null=True,blank=True)
    reopen_reason = models.CharField(max_length=240,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['agreement','month'],name='unique_settlement_agreement_month')]

class SettlementLine(models.Model):
    """Frozen statement rows (rebuilt each time approval is requested or the month closes) with a link back to the source record."""
    settlement = models.ForeignKey(Settlement,on_delete=models.CASCADE,related_name='lines')
    section = models.CharField(max_length=10)
    label = models.CharField(max_length=160)
    amount = models.DecimalField(max_digits=14,decimal_places=2)
    source_type = models.CharField(max_length=30,blank=True)
    source_id = models.CharField(max_length=40,blank=True)
    order = models.PositiveIntegerField(default=0)
    class Meta: ordering=['order']
