from decimal import Decimal
from django.conf import settings
from django.db import models
from .organizations import Organization
from .parties import Party, Vehicle
from .agreements import Agreement

class VehicleOwnership(models.Model):
    """Who owned a vehicle when. Ranges never overlap per vehicle (enforced in services.transfer_vehicle_ownership)."""
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle,on_delete=models.CASCADE,related_name='ownerships')
    owner = models.ForeignKey(Party,on_delete=models.PROTECT,related_name='ownerships')
    effective_from = models.DateField()
    effective_to = models.DateField(null=True,blank=True,help_text='Inclusive; null = current')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: indexes=[models.Index(fields=['vehicle','effective_from'])]

class DriverAssignment(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    agreement = models.ForeignKey(Agreement,on_delete=models.CASCADE,related_name='driver_assignments')
    driver = models.ForeignKey(Party,on_delete=models.PROTECT,related_name='assignments')
    effective_from = models.DateField()
    effective_to = models.DateField(null=True,blank=True,help_text='Inclusive; null = current')
    salary_override = models.DecimalField(max_digits=14,decimal_places=2,null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: indexes=[models.Index(fields=['agreement','effective_from'])]

class AgreementVersion(models.Model):
    """Effective-dated commercial terms. A version applies to every period starting on/after effective_from (always a 1st)."""
    class Status(models.TextChoices): DRAFT='draft','Draft'; ACTIVE='active','Active'; SUPERSEDED='superseded','Superseded'
    class Cadence(models.TextChoices): MONTHLY='monthly','Monthly'
    agreement = models.ForeignKey(Agreement,on_delete=models.CASCADE,related_name='versions')
    number = models.PositiveIntegerField()
    effective_from = models.DateField()
    customer_rent = models.DecimalField(max_digits=14,decimal_places=2)
    owner_rent = models.DecimalField(max_digits=14,decimal_places=2)
    driver_salary = models.DecimalField(max_digits=14,decimal_places=2)
    fuel_responsible = models.CharField(max_length=12,default='owner')
    repair_responsible = models.CharField(max_length=12,default='owner')
    owner_offset_allowed = models.BooleanField(default=False)
    driver_provider = models.CharField(max_length=10,default='operator')
    driver_paid_by = models.CharField(max_length=10,default='operator')
    clauses = models.TextField(blank=True)
    billing_cadence = models.CharField(max_length=10,choices=Cadence.choices,default=Cadence.MONTHLY)
    payout_cadence = models.CharField(max_length=10,choices=Cadence.choices,default=Cadence.MONTHLY)
    approval_threshold = models.DecimalField(max_digits=14,decimal_places=2,default=Decimal('5000.00'))
    status = models.CharField(max_length=12,choices=Status.choices,default=Status.ACTIVE)
    reason = models.CharField(max_length=240,blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['agreement','number'],name='unique_agreement_version_number')]
