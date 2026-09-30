import uuid
from datetime import date
from django.conf import settings
from django.db import models
from django.db.models import Q

def uid(): return uuid.uuid4().hex[:16].upper()

class Organization(models.Model):
    name = models.CharField(max_length=180)
    currency = models.CharField(max_length=3,default='BDT')
    created_at = models.DateTimeField(auto_now_add=True)

class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN='admin','Admin'; ACCOUNTANT='accountant','Accountant'; OPERATIONS='operations','Operations'; VIEWER='viewer','Viewer'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE,related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='rentflow_memberships')
    role = models.CharField(max_length=16,choices=Role.choices)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','user'],name='unique_org_user')]

class Party(models.Model):
    class Kind(models.TextChoices): OWNER='owner','Owner'; DRIVER='driver','Driver'; CUSTOMER='customer','Customer'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    kind = models.CharField(max_length=10,choices=Kind.choices)
    name = models.CharField(max_length=180)
    phone = models.CharField(max_length=32,blank=True)
    email = models.EmailField(blank=True)
    class Meta: indexes=[models.Index(fields=['organization','kind','name'])]

class Vehicle(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    registration = models.CharField(max_length=64)
    model_name = models.CharField(max_length=120)
    owner = models.ForeignKey(Party,on_delete=models.PROTECT,related_name='vehicles')
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','registration'],name='unique_registration_org')]

class Agreement(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle,on_delete=models.PROTECT)
    customer = models.ForeignKey(Party,on_delete=models.PROTECT,related_name='customer_agreements')
    driver = models.ForeignKey(Party,on_delete=models.PROTECT,related_name='driver_agreements')
    start_date = models.DateField()
    end_date = models.DateField(null=True,blank=True)
    customer_rent = models.DecimalField(max_digits=14,decimal_places=2)
    owner_rent = models.DecimalField(max_digits=14,decimal_places=2)
    driver_salary = models.DecimalField(max_digits=14,decimal_places=2)
    fuel_responsible = models.CharField(max_length=12,choices=[('owner','Owner'),('customer','Customer'),('operator','Operator')],default='owner')
    repair_responsible = models.CharField(max_length=12,choices=[('owner','Owner'),('customer','Customer'),('operator','Operator')],default='owner')
    owner_offset_allowed = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: indexes=[models.Index(fields=['organization','vehicle','start_date'])]

class Period(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    agreement = models.ForeignKey(Agreement,on_delete=models.PROTECT)
    month = models.DateField(help_text='First day of month')
    closed_at = models.DateTimeField(null=True,blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['agreement','month'],name='unique_agreement_month')]

class Obligation(models.Model):
    class Kind(models.TextChoices): CUSTOMER='customer_rent','Customer rent'; OWNER='owner_rent','Owner rent'; DRIVER='driver_salary','Driver salary'
    period = models.ForeignKey(Period,on_delete=models.PROTECT,related_name='obligations')
    kind = models.CharField(max_length=20,choices=Kind.choices)
    party = models.ForeignKey(Party,on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14,decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['period','kind'],name='unique_period_obligation')]

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

class Payment(models.Model):
    class Kind(models.TextChoices): CUSTOMER='customer_payment','Customer payment'; OWNER='owner_payout','Owner payout'; DRIVER='driver_payout','Driver payout'; ADVANCE='salary_advance','Salary advance'
    public_id = models.CharField(max_length=20,unique=True,default=uid)
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    kind = models.CharField(max_length=20,choices=Kind.choices)
    party = models.ForeignKey(Party,on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14,decimal_places=2)
    reference = models.CharField(max_length=120)
    method = models.CharField(max_length=30,default='bank')
    event_date = models.DateField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','reference'],name='unique_payment_org_reference')]

class Allocation(models.Model):
    payment = models.ForeignKey(Payment,on_delete=models.PROTECT,related_name='allocations')
    obligation = models.ForeignKey(Obligation,on_delete=models.PROTECT,related_name='allocations')
    amount = models.DecimalField(max_digits=14,decimal_places=2)
    class Meta: constraints=[models.UniqueConstraint(fields=['payment','obligation'],name='unique_payment_obligation')]

class OwnerOffset(models.Model):
    expense = models.OneToOneField(Expense,on_delete=models.PROTECT,related_name='owner_offset')
    obligation = models.ForeignKey(Obligation,on_delete=models.PROTECT,related_name='offsets')
    amount = models.DecimalField(max_digits=14,decimal_places=2)

class JournalEntry(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    source_type = models.CharField(max_length=30)
    source_id = models.CharField(max_length=40)
    posted_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','source_type','source_id'],name='unique_journal_source')]

class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry,on_delete=models.PROTECT,related_name='lines')
    account = models.CharField(max_length=40)
    debit = models.DecimalField(max_digits=14,decimal_places=2,default=0)
    credit = models.DecimalField(max_digits=14,decimal_places=2,default=0)

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

class IdempotencyRecord(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    action = models.CharField(max_length=40)
    key = models.CharField(max_length=120)
    payload_hash = models.CharField(max_length=64)
    object_id = models.PositiveBigIntegerField()
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','action','key'],name='unique_idempotency_key')]
