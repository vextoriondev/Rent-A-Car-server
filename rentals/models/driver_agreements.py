from django.conf import settings
from django.db import models
from .organizations import Organization, Branch
from .parties import Party, Vehicle

class DriverAgreement(models.Model):
    """A simple standalone agreement between the operator and a driver (salary and staff terms), made outside any vehicle agreement.
    It issues its own monthly periods that post to the same driver ledger; it has no customer and no owner side."""
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch,null=True,blank=True,on_delete=models.PROTECT,related_name='driver_agreements')
    driver = models.ForeignKey(Party,on_delete=models.PROTECT,related_name='standalone_driver_agreements')
    vehicle = models.ForeignKey(Vehicle,null=True,blank=True,on_delete=models.PROTECT,related_name='+',help_text='Optional: the vehicle the driver is usually assigned to')
    start_date = models.DateField()
    end_date = models.DateField(null=True,blank=True)
    salary = models.DecimalField(max_digits=14,decimal_places=2,help_text='Monthly salary effective today (mirrors the current version)')
    terms = models.TextField(blank=True,help_text='Free-text staff arrangement: duties, days off, allowances')
    status = models.CharField(max_length=10,choices=[('active','Active'),('ended','Ended')],default='active')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def save(self,*args,**kwargs):
        if self.branch_id is None:
            self.branch=self.vehicle.branch if self.vehicle_id else Branch.objects.get_or_create(organization_id=self.organization_id,name='Main')[0]
        super().save(*args,**kwargs)
    class Meta: indexes=[models.Index(fields=['organization','driver','start_date'])]

class DriverAgreementVersion(models.Model):
    """Effective-dated salary and terms; a version applies to every month starting on or after effective_from (a 1st, except version 1)."""
    driver_agreement = models.ForeignKey(DriverAgreement,on_delete=models.CASCADE,related_name='versions')
    number = models.PositiveIntegerField()
    effective_from = models.DateField()
    salary = models.DecimalField(max_digits=14,decimal_places=2)
    terms = models.TextField(blank=True)
    reason = models.CharField(max_length=240,blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['driver_agreement','number'],name='unique_driver_agreement_version_number')]
