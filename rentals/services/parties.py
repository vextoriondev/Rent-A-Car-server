"""Identity services: parties, contacts, consent, actor logins, branches. Organization is always passed in, never read from the payload."""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from ..models import Branch, Contact, Membership, Party, PartyUser
from .audit import audit
from .helpers import DomainError

CHANNELS={'sms','whatsapp','email','none'}

def default_branch(org):
    return Branch.objects.get_or_create(organization=org,name='Main')[0]

def update_party(org,party_id,data,actor=None):
    party=Party.objects.get(pk=party_id,organization=org)
    before={'name':party.name,'phone':party.phone,'email':party.email}
    for field in ('name','phone','email'):
        if data.get(field) is not None:setattr(party,field,data[field].strip())
    if not party.name:raise DomainError('Name is required')
    party.save()
    audit(org,actor,'party.update','party',party.pk,before=before,after={'name':party.name,'phone':party.phone,'email':party.email})
    return party

def set_consent(org,party_id,*,sms=None,whatsapp=None,email=None,preferred_channel=None,source='',actor=None):
    party=Party.objects.get(pk=party_id,organization=org)
    before={'preferred_channel':party.preferred_channel,'sms':party.consent_sms,'whatsapp':party.consent_whatsapp,'email':party.consent_email}
    if preferred_channel is not None:
        if preferred_channel not in CHANNELS:raise DomainError('Unknown channel')
        party.preferred_channel=preferred_channel
    for name,value in (('consent_sms',sms),('consent_whatsapp',whatsapp),('consent_email',email)):
        if value is not None:setattr(party,name,value)
    consented={'sms':party.consent_sms,'whatsapp':party.consent_whatsapp,'email':party.consent_email}
    if party.preferred_channel!='none' and not consented[party.preferred_channel]:raise DomainError('Preferred channel needs consent')
    party.consent_updated_at=timezone.now();party.consent_source=source[:120];party.save()
    audit(org,actor,'party.consent','party',party.pk,before=before,after={'preferred_channel':party.preferred_channel,'sms':party.consent_sms,'whatsapp':party.consent_whatsapp,'email':party.consent_email},reason=source)
    return party

def add_contact(org,party_id,data,actor=None):
    party=Party.objects.get(pk=party_id,organization=org)
    if not data['name'].strip():raise DomainError('Name is required')
    role=data.get('role','other')
    if role not in Contact.Role.values:raise DomainError('Unknown contact role')
    contact=Contact.objects.create(party=party,name=data['name'].strip(),phone=data.get('phone',''),email=data.get('email',''),role=role)
    audit(org,actor,'party.contact_add','party',party.pk,after={'contact_id':contact.pk,'name':contact.name,'role':role})
    return contact

def delete_contact(org,party_id,contact_id,actor=None):
    contact=Contact.objects.get(pk=contact_id,party_id=party_id,party__organization=org)
    info={'contact_id':contact.pk,'name':contact.name,'role':contact.role}
    contact.delete()
    audit(org,actor,'party.contact_delete','party',party_id,before=info)

@transaction.atomic
def grant_party_access(org,party_id,username,branch_id=None,actor=None):
    """Link an existing login to a party; the membership role mirrors the party kind so it can only ever be an actor."""
    party=Party.objects.get(pk=party_id,organization=org)
    user=get_user_model().objects.filter(username=username).first()
    if not user:raise DomainError('User not found')
    member=Membership.objects.filter(organization=org,user=user).first()
    if member and member.role!=party.kind:raise DomainError('That user already has a different role in this organization')
    if not member:Membership.objects.create(organization=org,user=user,role=party.kind,branch_id=branch_id)
    link,_=PartyUser.objects.update_or_create(party=party,user=user,defaults={'organization':org,'active':True})
    audit(org,actor,'party.access_grant','party',party.pk,after={'user_id':user.pk,'role':party.kind})
    return link

def revoke_party_access(org,party_id,user_id,actor=None):
    PartyUser.objects.filter(organization=org,party_id=party_id,user_id=user_id).update(active=False)
    audit(org,actor,'party.access_revoke','party',party_id,after={'user_id':user_id})

def create_branch(org,name,actor=None):
    name=name.strip()
    if not name:raise DomainError('Branch name is required')
    branch=Branch.objects.create(organization=org,name=name)
    audit(org,actor,'branch.create','branch',branch.pk,after={'name':name})
    return branch
