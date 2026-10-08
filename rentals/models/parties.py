from django.conf import settings
from django.db import models
from .organizations import Organization, Branch

class Party(models.Model):
    class Kind(models.TextChoices): OWNER='owner','Owner'; DRIVER='driver','Driver'; CUSTOMER='customer','Customer'
    class Channel(models.TextChoices): SMS='sms','SMS'; WHATSAPP='whatsapp','WhatsApp'; EMAIL='email','Email'; NONE='none','None'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    kind = models.CharField(max_length=10,choices=Kind.choices)
    name = models.CharField(max_length=180)
    phone = models.CharField(max_length=32,blank=True)
    email = models.EmailField(blank=True)
    preferred_channel = models.CharField(max_length=10,choices=Channel.choices,default=Channel.NONE)
    consent_sms = models.BooleanField(default=False)
    consent_whatsapp = models.BooleanField(default=False)
    consent_email = models.BooleanField(default=False)
    consent_updated_at = models.DateTimeField(null=True,blank=True)
    consent_source = models.CharField(max_length=120,blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name='+',help_text='Lets a branch-limited user see a party they created before it is linked to an agreement')
    class Meta: indexes=[models.Index(fields=['organization','kind','name'])]

class Contact(models.Model):
    """Named people at a party, e.g. the billing contact of a corporate customer."""
    class Role(models.TextChoices): BILLING='billing','Billing'; OPERATIONS='operations','Operations'; PRIMARY='primary','Primary'; OTHER='other','Other'
    party = models.ForeignKey(Party,on_delete=models.CASCADE,related_name='contacts')
    name = models.CharField(max_length=180)
    phone = models.CharField(max_length=32,blank=True)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=12,choices=Role.choices,default=Role.OTHER)

class PartyUser(models.Model):
    """Lets a login act as an owner, driver or customer; scope is limited to that party's own records."""
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    party = models.ForeignKey(Party,on_delete=models.CASCADE,related_name='logins')
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='rentflow_party_links')
    active = models.BooleanField(default=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['party','user'],name='unique_party_user')]

class Vehicle(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    registration = models.CharField(max_length=64)
    model_name = models.CharField(max_length=120)
    owner = models.ForeignKey(Party,on_delete=models.PROTECT,related_name='vehicles')
    branch = models.ForeignKey('rentals.Branch',null=True,blank=True,on_delete=models.PROTECT,related_name='vehicles')
    def save(self,*args,**kwargs):
        if self.branch_id is None and self.organization_id:  # keep every vehicle in a branch, whichever code path created it
            self.branch=Branch.objects.get_or_create(organization_id=self.organization_id,name='Main')[0]
        super().save(*args,**kwargs)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','registration'],name='unique_registration_org')]
