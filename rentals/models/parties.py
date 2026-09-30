from django.db import models
from .organizations import Organization

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
