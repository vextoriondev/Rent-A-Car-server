"""Garage / vendor payables ledger, organization finance settings and login throttling (final completion round)."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from .organizations import Organization
from .parties import Vehicle


class GarageLedgerEntry(models.Model):
    """Append-only payable ledger per garage. `delta` > 0 means we owe the garage more (bill), < 0 means we paid/credited.
    The legacy Garage.total_bill / total_paid are caches rewritten from this table."""
    class EntryType(models.TextChoices):
        BILL = 'bill', 'Bill (maintenance work)'
        ADJUSTMENT = 'adjustment', 'Bill adjustment'
        PAYMENT = 'payment', 'Payment'
        REVERSAL = 'reversal', 'Reversal'
        LEGACY = 'legacy', 'Migrated legacy balance'

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='garage_ledger')
    garage = models.ForeignKey('rentals.Garage', on_delete=models.PROTECT, related_name='ledger_entries')
    entry_type = models.CharField(max_length=12, choices=EntryType.choices)
    delta = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField()
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    maintenance_id = models.PositiveIntegerField(null=True, blank=True, help_text='MaintenanceRecord id (plain integer: the record may later be deleted)')
    payment_method = models.CharField(max_length=40, blank=True)
    reference = models.CharField(max_length=120, blank=True, help_text='Vendor invoice / voucher number')
    description = models.TextField(blank=True)
    attachment = models.CharField(max_length=255, blank=True)
    unique_key = models.CharField(max_length=160, null=True, blank=True)
    reverses = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='reversed_by')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        indexes = [models.Index(fields=['organization', 'garage'])]
        constraints = [
            models.UniqueConstraint(fields=['organization', 'unique_key'], condition=Q(unique_key__isnull=False), name='uniq_garage_ledger_key'),
            models.CheckConstraint(condition=~Q(delta=0), name='garage_ledger_delta_nonzero'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError('Garage ledger entries are append-only; post a reversal instead')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError('Garage ledger entries are append-only; post a reversal instead')


class FinanceSettings(models.Model):
    """Per-organization, auditable accounting policy. Defaults keep the pre-existing behaviour (provisional until the client confirms)."""
    class Proration(models.TextChoices):
        FULL_MONTH = 'full_month', 'Full month'
        CALENDAR_DAYS = 'calendar_days', 'Daily, actual calendar days'
        THIRTY_DAY = 'thirty_day', 'Daily, fixed 30-day basis'

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='finance_settings')
    rent_proration = models.CharField(max_length=16, choices=Proration.choices, default=Proration.FULL_MONTH)
    fuel_default_direction = models.CharField(max_length=16, default='driver_owes')            # driver_owes | company_owes
    fuel_default_settlement = models.CharField(max_length=20, default='carry_forward')          # salary_deduction | cash | carry_forward
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)


class LoginThrottle(models.Model):
    """Failed-login counter per (username, ip). Stored in the database so it holds across workers."""
    key = models.CharField(max_length=200, unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_started = models.DateTimeField()
    locked_until = models.DateTimeField(null=True, blank=True)
