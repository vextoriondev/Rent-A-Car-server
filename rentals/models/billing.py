from django.conf import settings
from django.db import models
from .organizations import Organization
from .parties import Party
from .agreements import Agreement, Period, Obligation

class Charge(models.Model):
    """A variable charge, credit, adjustment, reimbursement or opening/carried balance posted against one side of a period.
    Posting creates one Obligation (signed amount) and one journal entry; charges are never edited."""
    class Kind(models.TextChoices):
        CHARGE='charge','Charge'; CREDIT='credit','Credit'; ADJUSTMENT='adjustment','Adjustment'; REIMBURSEMENT='reimbursement','Reimbursement'; EXPENSE_FRONT='expense_front','Expense paid by a third party'
        OPENING='opening_balance','Opening balance'; CARRY_OUT='carry_out','Carried forward'
    class Direction(models.TextChoices): INCREASE='increase','Increase'; DECREASE='decrease','Decrease'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    kind = models.CharField(max_length=20,choices=Kind.choices)
    direction = models.CharField(max_length=10,choices=Direction.choices)
    side = models.CharField(max_length=10)
    party = models.ForeignKey(Party,on_delete=models.PROTECT)
    agreement = models.ForeignKey(Agreement,on_delete=models.PROTECT)
    period = models.ForeignKey(Period,on_delete=models.PROTECT,related_name='charges')
    amount = models.DecimalField(max_digits=14,decimal_places=2)
    description = models.CharField(max_length=240,blank=True)
    reference = models.CharField(max_length=120,blank=True)
    obligation = models.OneToOneField(Obligation,null=True,blank=True,on_delete=models.PROTECT,related_name='charge')
    source_type = models.CharField(max_length=30,blank=True)
    source_id = models.CharField(max_length=60,blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','source_type','source_id'],condition=~models.Q(source_type=''),name='unique_charge_source')]

class InvoiceSequence(models.Model):
    organization = models.OneToOneField(Organization,on_delete=models.CASCADE,related_name='invoice_sequence')
    last_number = models.PositiveIntegerField(default=0)

class Invoice(models.Model):
    """Issued/void is stored; paid, partially_paid and overdue are derived from allocations (services.invoice_summary)."""
    class State(models.TextChoices): ISSUED='issued','Issued'; VOID='void','Void'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    number = models.CharField(max_length=30)
    party = models.ForeignKey(Party,on_delete=models.PROTECT)
    agreement = models.ForeignKey(Agreement,null=True,blank=True,on_delete=models.PROTECT)
    period = models.ForeignKey(Period,null=True,blank=True,on_delete=models.PROTECT)
    issue_date = models.DateField()
    due_date = models.DateField()
    state = models.CharField(max_length=8,choices=State.choices,default=State.ISSUED)
    void_reason = models.CharField(max_length=240,blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','number'],name='unique_invoice_number_org')]

class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice,on_delete=models.CASCADE,related_name='lines')
    obligation = models.ForeignKey(Obligation,on_delete=models.PROTECT,related_name='invoice_lines')
    description = models.CharField(max_length=240,blank=True)
    amount = models.DecimalField(max_digits=14,decimal_places=2)
    class Meta: constraints=[models.UniqueConstraint(fields=['invoice','obligation'],name='unique_invoice_line_obligation')]
