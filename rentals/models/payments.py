from django.conf import settings
from django.db import models
from .base import uid
from .organizations import Organization
from .parties import Party
from .agreements import Obligation

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
