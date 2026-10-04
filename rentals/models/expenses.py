from datetime import date
from decimal import Decimal
from django.conf import settings
from django.db import models
from .base import uid
from .organizations import Organization
from .agreements import Period, Obligation
from .terms import AgreementVersion

class ExpenseCategory(models.Model):
    """Organization-configurable categories. `code` is what the API and the frontend send as `category`."""
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE,related_name='expense_categories')
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=80)
    active = models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','code'],name='unique_expense_category_code')]

class ExpenseRule(models.Model):
    """Who pays and who is liable for one category under one agreement version. Frozen once a period was issued under that version."""
    class Deductible(models.TextChoices): OWNER_PAYABLE='owner_payable','Owner payable'; NONE='none','None'
    agreement_version = models.ForeignKey(AgreementVersion,on_delete=models.CASCADE,related_name='expense_rules')
    category = models.CharField(max_length=40)
    responsible = models.CharField(max_length=12)
    first_payer = models.CharField(max_length=12,default='operator')
    reimbursable = models.BooleanField(default=True)
    deductible_from = models.CharField(max_length=14,choices=Deductible.choices,default=Deductible.NONE)
    approval_threshold = models.DecimalField(max_digits=14,decimal_places=2,null=True,blank=True,help_text='null = the version threshold')
    proof_required = models.BooleanField(default=False)
    notify_parties = models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['agreement_version','category'],name='unique_rule_version_category')]

class Expense(models.Model):
    class State(models.TextChoices): DRAFT='draft','Draft'; PENDING='pending','Pending approval'; POSTED='posted','Posted'; REVERSED='reversed','Reversed'
    public_id = models.CharField(max_length=20,unique=True,default=uid)
    period = models.ForeignKey(Period,on_delete=models.PROTECT,related_name='expenses')
    adjusts_period = models.ForeignKey(Period,null=True,blank=True,on_delete=models.PROTECT,related_name='late_adjustments',help_text='Closed period this late entry belongs to; the expense itself posts in the next open period')
    maintenance_case = models.ForeignKey('rentals.MaintenanceCase',null=True,blank=True,on_delete=models.PROTECT,related_name='expenses')
    category = models.CharField(max_length=40)
    category_ref = models.ForeignKey(ExpenseCategory,null=True,blank=True,on_delete=models.PROTECT,related_name='expenses')
    amount = models.DecimalField(max_digits=14,decimal_places=2)
    event_date = models.DateField(default=date.today)
    paid_by = models.CharField(max_length=12)
    responsible = models.CharField(max_length=12)
    offset_owner = models.BooleanField(default=False)
    rule = models.ForeignKey(ExpenseRule,null=True,blank=True,on_delete=models.PROTECT,related_name='expenses')
    rule_snapshot = models.JSONField(default=dict)
    reference = models.CharField(max_length=120)
    receipt_reference = models.CharField(max_length=240,blank=True)
    description = models.TextField(blank=True)
    state = models.CharField(max_length=12,choices=State.choices,default=State.PENDING)
    override_reason = models.TextField(blank=True)
    overridden_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='overridden_expenses')
    duplicate_reason = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='recorded_expenses')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='approved_expenses')
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='reversed_expenses')
    reversed_at = models.DateTimeField(null=True,blank=True)
    reversal_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['period','reference'],name='unique_expense_period_reference')]

class Approval(models.Model):
    """One approval request per subject (currently expenses); the decision is recorded, never overwritten."""
    class Decision(models.TextChoices): PENDING='pending','Pending'; APPROVED='approved','Approved'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    subject_type = models.CharField(max_length=30)
    subject_id = models.CharField(max_length=40)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    decision = models.CharField(max_length=10,choices=Decision.choices,default=Decision.PENDING)
    reason = models.TextField(blank=True)
    threshold_at_time = models.DecimalField(max_digits=14,decimal_places=2,default=Decimal('0.00'))
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','subject_type','subject_id'],name='unique_approval_subject')]

class OwnerOffset(models.Model):
    expense = models.OneToOneField(Expense,on_delete=models.PROTECT,related_name='owner_offset')
    obligation = models.ForeignKey(Obligation,on_delete=models.PROTECT,related_name='offsets')
    amount = models.DecimalField(max_digits=14,decimal_places=2)
