from datetime import date
from django.conf import settings
from django.db import models
from .base import uid
from .agreements import Period, Obligation

class Expense(models.Model):
    class State(models.TextChoices): PENDING='pending','Pending approval'; POSTED='posted','Posted'; REVERSED='reversed','Reversed'
    public_id = models.CharField(max_length=20,unique=True,default=uid)
    period = models.ForeignKey(Period,on_delete=models.PROTECT,related_name='expenses')
    category = models.CharField(max_length=40)
    amount = models.DecimalField(max_digits=14,decimal_places=2)
    event_date = models.DateField(default=date.today)
    paid_by = models.CharField(max_length=12)
    responsible = models.CharField(max_length=12)
    offset_owner = models.BooleanField(default=False)
    rule_snapshot = models.JSONField(default=dict)
    reference = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    state = models.CharField(max_length=12,choices=State.choices,default=State.PENDING)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='recorded_expenses')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='approved_expenses')
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='reversed_expenses')
    reversed_at = models.DateTimeField(null=True,blank=True)
    reversal_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['period','reference'],name='unique_expense_period_reference')]

class OwnerOffset(models.Model):
    expense = models.OneToOneField(Expense,on_delete=models.PROTECT,related_name='owner_offset')
    obligation = models.ForeignKey(Obligation,on_delete=models.PROTECT,related_name='offsets')
    amount = models.DecimalField(max_digits=14,decimal_places=2)
