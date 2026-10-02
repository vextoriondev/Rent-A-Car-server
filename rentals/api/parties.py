from django.db import IntegrityError
from ninja import Router
from ninja.errors import HttpError
from .. import policies, services
from ..models import Branch, Party, Vehicle
from ._shared import PartyIn, PartyPatch, VehicleIn, ConsentIn, ContactIn, AccessIn, BranchIn, membership, result, party_dict

router = Router()

@router.get('/orgs/{org_id}/parties')
def parties(request,org_id:int,kind:str|None=None):
    m=membership(request,org_id,'read')
    qs=policies.scope_queryset(request.user,m,Party.objects.all())
    if kind:qs=qs.filter(kind=kind)
    sensitive=policies.can(m.role,'sensitive')
    if sensitive:qs=qs.prefetch_related('contacts')
    return [party_dict(p,sensitive) for p in qs.order_by('name')[:200]]

@router.post('/orgs/{org_id}/parties')
def create_party(request,org_id:int,payload:PartyIn):
    membership(request,org_id,'party.write')
    if not payload.name.strip():raise HttpError(422,'Name is required')
    p=Party.objects.create(organization_id=org_id,**payload.dict())
    return {'id':p.pk,'kind':p.kind,'name':p.name}

@router.get('/orgs/{org_id}/vehicles')
def vehicles(request,org_id:int):
    m=membership(request,org_id,'read')
    return [{'id':v.pk,'registration':v.registration,'model_name':v.model_name,'owner_id':v.owner_id,'owner':v.owner.name} for v in policies.scope_queryset(request.user,m,Vehicle.objects.all()).select_related('owner').order_by('registration')[:200]]

@router.post('/orgs/{org_id}/vehicles')
def create_vehicle(request,org_id:int,payload:VehicleIn):
    membership(request,org_id,'vehicle.write')
    owner=Party.objects.filter(pk=payload.owner_id,organization_id=org_id,kind=Party.Kind.OWNER).first()
    if not owner:raise HttpError(422,'Owner is invalid')
    try:v=Vehicle.objects.create(organization_id=org_id,registration=payload.registration.strip(),model_name=payload.model_name.strip(),owner=owner)
    except IntegrityError:raise HttpError(409,'Registration already exists')
    return {'id':v.pk,'registration':v.registration,'model_name':v.model_name}

@router.patch('/orgs/{org_id}/parties/{party_id}')
def update_party(request,org_id:int,party_id:int,payload:PartyPatch):
    m=membership(request,org_id,'party.write')
    return party_dict(result(services.update_party,m.organization,party_id,payload.dict()))

@router.put('/orgs/{org_id}/parties/{party_id}/consent')
def set_consent(request,org_id:int,party_id:int,payload:ConsentIn):
    m=membership(request,org_id,'sensitive')
    return party_dict(result(services.set_consent,m.organization,party_id,**payload.dict()),sensitive=True)

@router.post('/orgs/{org_id}/parties/{party_id}/contacts')
def add_contact(request,org_id:int,party_id:int,payload:ContactIn):
    m=membership(request,org_id,'party.write')
    c=result(services.add_contact,m.organization,party_id,payload.dict())
    return {'id':c.pk,'name':c.name,'phone':c.phone,'email':c.email,'role':c.role}

@router.delete('/orgs/{org_id}/parties/{party_id}/contacts/{contact_id}')
def delete_contact(request,org_id:int,party_id:int,contact_id:int):
    m=membership(request,org_id,'party.write')
    result(services.delete_contact,m.organization,party_id,contact_id)
    return {'deleted':True}

@router.post('/orgs/{org_id}/parties/{party_id}/access')
def grant_access(request,org_id:int,party_id:int,payload:AccessIn):
    m=membership(request,org_id,'access.manage')
    link=result(services.grant_party_access,m.organization,party_id,payload.username,payload.branch_id)
    return {'party_id':link.party_id,'user_id':link.user_id,'active':link.active}

@router.delete('/orgs/{org_id}/parties/{party_id}/access/{user_id}')
def revoke_access(request,org_id:int,party_id:int,user_id:int):
    m=membership(request,org_id,'access.manage')
    services.revoke_party_access(m.organization,party_id,user_id)
    return {'revoked':True}

@router.get('/orgs/{org_id}/branches')
def branches(request,org_id:int):
    m=membership(request,org_id,'read_financial')
    return [{'id':b.pk,'name':b.name} for b in Branch.objects.filter(organization=m.organization).order_by('name')]

@router.post('/orgs/{org_id}/branches')
def create_branch(request,org_id:int,payload:BranchIn):
    m=membership(request,org_id,'branch.write')
    b=result(services.create_branch,m.organization,payload.name)
    return {'id':b.pk,'name':b.name}
