from django.conf import settings
from django.db import models

class Organization(models.Model):
    name = models.CharField(max_length=180)
    currency = models.CharField(max_length=3,default='BDT')
    created_at = models.DateTimeField(auto_now_add=True)

class Branch(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE,related_name='branches')
    name = models.CharField(max_length=120)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','name'],name='unique_branch_name_org')]

class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN='admin','Admin'; ACCOUNTANT='accountant','Accountant'; OPERATIONS='operations','Operations'; VIEWER='viewer','Viewer'
        MECHANIC='mechanic','Mechanic'; OWNER='owner','Owner'; DRIVER='driver','Driver'; CUSTOMER='customer','Customer'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE,related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='rentflow_memberships')
    role = models.CharField(max_length=16,choices=Role.choices)
    branch = models.ForeignKey(Branch,null=True,blank=True,on_delete=models.SET_NULL,related_name='memberships')
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','user'],name='unique_org_user')]
