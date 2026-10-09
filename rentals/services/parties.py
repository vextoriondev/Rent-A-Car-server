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

@transaction.atomic
def update_member(org,actor,member_id,role=None,branch_id=None,clear_branch=False):
    """Change a staff member's role or branch. Owner/driver/customer are not grantable roles, and the last admin cannot be demoted."""
    members=Membership.objects.select_for_update(of=('self',)).filter(organization=org)
    target=members.select_related('user','branch').filter(pk=member_id,role__in=['admin','accountant','operations','viewer','mechanic']).first()
    if not target:raise Membership.DoesNotExist
    before={'role':target.role,'branch_id':target.branch_id}
    if role is not None and role!=target.role:
        if target.role=='admin' and not members.filter(role='admin').exclude(pk=target.pk).exists():raise DomainError('An organization needs at least one admin')
        target.role=role
    if clear_branch:target.branch=None
    elif branch_id is not None:
        branch=Branch.objects.filter(pk=branch_id,organization=org).first()
        if not branch:raise DomainError('Branch is invalid')
        target.branch=branch
    target.save()
    audit(org,actor,'member.update','membership',target.pk,before=before,after={'role':target.role,'branch_id':target.branch_id})
    return target

def mask_id(value):
    """Last four characters only; short values are fully hidden."""
    return '*'*(len(value)-4)+value[-4:] if len(value)>4 else ('*'*len(value))

def party_ids(org,party_id,actor,full):
    """Protected identity numbers; every read is logged and the audit keeps no value."""
    party=Party.objects.get(pk=party_id,organization=org)
    audit(org,actor,'sensitive.read','party_ids',party.pk,after={'masked':not full})
    nid,lic=party.nid_number,party.driving_licence_number
    return {'party_id':party.pk,'nid_number':nid if full else mask_id(nid),'driving_licence_number':lic if full else mask_id(lic),'masked':not full}

def set_party_ids(org,party_id,data,actor):
    """Admin only (enforced by the policy action). Only the fields sent change; the audit stores masked values."""
    party=Party.objects.get(pk=party_id,organization=org)
    before={'nid_number':mask_id(party.nid_number),'driving_licence_number':mask_id(party.driving_licence_number)}
    for field in('nid_number','driving_licence_number'):
        if data.get(field) is not None:
            value=data[field].strip()
            if len(value)>40:raise DomainError('An identity number may have at most 40 characters')
            setattr(party,field,value)
    party.save(update_fields=['nid_number','driving_licence_number'])
    audit(org,actor,'party.ids_update','party',party.pk,before=before,after={'nid_number':mask_id(party.nid_number),'driving_licence_number':mask_id(party.driving_licence_number)})
    return party
