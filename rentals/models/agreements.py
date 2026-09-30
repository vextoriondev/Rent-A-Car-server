from django.conf import settings
from django.db import models
from .organizations import Organization
from .parties import Party, Vehicle

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
