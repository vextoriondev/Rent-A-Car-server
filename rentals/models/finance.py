"""Party ledger + workflows added for the client change request (driver salary/advance/fuel-gas, owner & client
opening balances, rent accrual, replacement drivers, owner/client agreements).

`PartyLedgerEntry` is the single source of truth for every balance owed to / by an owner, client or driver.
The legacy counters on `PartyDetail` are caches that `services.finance` rewrites from this ledger.
Rows are append-only: corrections are reversal rows, never edits or deletes.
"""
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models import Q
from .organizations import Organization
from .parties import Party, Vehicle


class PartyLedgerEntry(models.Model):
    class Account(models.TextChoices):
        OWNER_PAYABLE = 'owner_payable', 'Owner payable (+ = we owe the owner)'
        CLIENT_RECEIVABLE = 'client_receivable', 'Client receivable (+ = client owes us)'
        DRIVER_SALARY = 'driver_salary', 'Driver salary due (+ = we owe the driver)'
        DRIVER_ADVANCE = 'driver_advance', 'Driver advance (+ = driver owes us)'
        DRIVER_FUEL_GAS = 'driver_fuel_gas', 'Fuel/Gas money held by driver (+ = driver owes us)'
        DRIVER_FUEL_REIMB = 'driver_fuel_reimb', 'Fuel/Gas reimbursable (+ = we owe the driver)'
        PARTY_OTHER_OWES_US = 'party_other_receivable', 'Other balance (+ = party owes us)'
        PARTY_OTHER_WE_OWE = 'party_other_payable', 'Other balance (+ = we owe the party)'

    class EntryType(models.TextChoices):
        OPENING = 'opening', 'Opening balance'
        ACCRUAL = 'accrual', 'Accrual'
        INVOICE = 'invoice', 'Invoice'
        ISSUE = 'issue', 'Issued'
        PAYMENT = 'payment', 'Payment / collection'
        SETTLEMENT = 'settlement', 'Settlement / adjustment against salary'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        REVERSAL = 'reversal', 'Reversal'
        LEGACY = 'legacy', 'Migrated legacy balance'

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='party_ledger')
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='ledger_entries')
    account = models.CharField(max_length=30, choices=Account.choices)
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    delta = models.DecimalField(max_digits=14, decimal_places=2, help_text='Signed: + increases the account balance, - reduces it')
    date = models.DateField()
    period = models.CharField(max_length=7, blank=True, help_text='YYYY-MM for monthly entries')
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    payment_method = models.CharField(max_length=40, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    attachment = models.CharField(max_length=255, blank=True)
    unique_key = models.CharField(max_length=160, null=True, blank=True, help_text='Idempotency token; unique per organization when set')
    reverses = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='reversed_by')
    source_type = models.CharField(max_length=30, blank=True)
    source_id = models.CharField(max_length=40, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        indexes = [models.Index(fields=['organization', 'party', 'account']), models.Index(fields=['organization', 'date'])]
        constraints = [
            models.UniqueConstraint(fields=['organization', 'unique_key'], condition=Q(unique_key__isnull=False), name='uniq_ledger_unique_key'),
            models.CheckConstraint(condition=~Q(delta=0), name='ledger_delta_nonzero'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError('Party ledger entries are append-only; post a reversal instead')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError('Party ledger entries are append-only; post a reversal instead')


class SalaryPayment(models.Model):
    class FuelMode(models.TextChoices):
        NONE = 'none', 'No fuel/gas settlement'
        SALARY_DEDUCTION = 'salary_deduction', 'Deducted from salary'
        CASH = 'cash', 'Settled in cash outside salary'
        SALARY_ADDITION = 'salary_addition', 'Reimbursement added to salary'

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='salary_payments')
    driver = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='salary_payments')
    salary_month = models.CharField(max_length=7, help_text='YYYY-MM')
    monthly_salary = models.DecimalField(max_digits=14, decimal_places=2)
    previous_salary_due = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    advance_balance_before = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    advance_adjusted = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fuel_gas_balance_before = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fuel_gas_account = models.CharField(max_length=30, default='driver_fuel_gas')
    fuel_gas_settled = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fuel_gas_mode = models.CharField(max_length=20, choices=FuelMode.choices, default=FuelMode.NONE)
    net_payable = models.DecimalField(max_digits=14, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2)
    remaining_salary_due = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payment_date = models.DateField()
    payment_method = models.CharField(max_length=40, default='Cash')
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=120, null=True, blank=True)
    status = models.CharField(max_length=10, default='posted')  # posted | reversed
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-salary_month', '-id']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'driver', 'salary_month'], condition=Q(status='posted'), name='uniq_salary_payment_month'),
            models.UniqueConstraint(fields=['organization', 'idempotency_key'], condition=Q(idempotency_key__isnull=False), name='uniq_salary_payment_idem'),
        ]


class RentAccrual(models.Model):
    """One row per owner+vehicle+month of monthly rent posted to the owner's payable."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='rent_accruals')
    owner = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='rent_accruals')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='rent_accruals')
    period = models.CharField(max_length=7, help_text='YYYY-MM')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, default='posted')  # posted | reversed
    reversal_reason = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period', 'vehicle_id']
        constraints = [models.UniqueConstraint(fields=['organization', 'owner', 'vehicle', 'period'], condition=Q(status='posted'), name='uniq_rent_accrual_period')]


class ReplacementAssignment(models.Model):
    """Which driver covered for which regular driver, on which date, in which vehicle. Informational: never changes salary."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='replacement_assignments')
    date = models.DateField()
    original_driver = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='replaced_on')
    replacement_driver = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='replacing_on')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='replacements')
    reason = models.CharField(max_length=240, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'vehicle', 'date'], name='uniq_replacement_vehicle_date'),
            models.UniqueConstraint(fields=['organization', 'original_driver', 'date'], name='uniq_replacement_original_date'),
            models.CheckConstraint(condition=~Q(original_driver=models.F('replacement_driver')), name='replacement_not_self'),
        ]


class TasinAgreement(models.Model):
    """Client or Owner agreement document with default + custom terms. Separate from the RentFlow billing `Agreement` engine."""
    class Type(models.TextChoices):
        CLIENT = 'client', 'Client Agreement'
        OWNER = 'owner', 'Owner Agreement'

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='tasin_agreements')
    agreement_type = models.CharField(max_length=10, choices=Type.choices)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='tasin_agreements')
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    title = models.CharField(max_length=220)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    default_terms = models.JSONField(default=list, blank=True)
    custom_terms = models.JSONField(default=list, blank=True, help_text='Ordered list of free-text terms (line breaks preserved)')
    final_content = models.TextField(blank=True, help_text='Generated agreement text saved at finalization')
    attachment = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, default='draft')  # draft | final | ended
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date', '-id']
