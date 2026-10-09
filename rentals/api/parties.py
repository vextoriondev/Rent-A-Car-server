from django.db import IntegrityError
from ninja import Router
from ninja.errors import HttpError
from .. import policies, services
from ..models import Branch, Membership, Party, Vehicle
from ._shared import PartyIn, PartyPatch, VehicleIn, ConsentIn, ContactIn, AccessIn, BranchIn, MemberPatch, IdsIn, membership, pick_branch, result, party_dict, paginate

router = Router()

@router.get('/orgs/{org_id}/parties')
def parties(request,org_id:int,kind:str|None=None,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read')
    qs=policies.scope_queryset(request.user,m,Party.objects.all(),branch_id)
    if kind:qs=qs.filter(kind=kind)
    sensitive=policies.can(m.role,'sensitive')
    if sensitive:
        qs=qs.prefetch_related('contacts')
        services.audit(m.organization,request.user,'sensitive.read','party_list')
    return paginate(qs.order_by('name'),limit,offset,lambda p:party_dict(p,sensitive))

@router.post('/orgs/{org_id}/parties')
def create_party(request,org_id:int,payload:PartyIn):
    membership(request,org_id,'party.write')
    if not payload.name.strip():raise HttpError(422,'Name is required')
    p=Party.objects.create(organization_id=org_id,created_by=request.user,**payload.dict())
    return {'id':p.pk,'kind':p.kind,'name':p.name}

@router.get('/orgs/{org_id}/vehicles')
def vehicles(request,org_id:int,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read')
    return paginate(policies.scope_queryset(request.user,m,Vehicle.objects.all(),branch_id).select_related('owner').order_by('registration'),limit,offset,lambda v:{'id':v.pk,'registration':v.registration,'model_name':v.model_name,'owner_id':v.owner_id,'owner':v.owner.name,'branch_id':v.branch_id})

@router.post('/orgs/{org_id}/vehicles')
def create_vehicle(request,org_id:int,payload:VehicleIn):
    m=membership(request,org_id,'vehicle.write')
    owner=Party.objects.filter(pk=payload.owner_id,organization_id=org_id,kind=Party.Kind.OWNER).first()
    if not owner:raise HttpError(422,'Owner is invalid')
    branch=pick_branch(m,payload.branch_id)
    try:v=Vehicle.objects.create(organization_id=org_id,registration=payload.registration.strip(),model_name=payload.model_name.strip(),owner=owner,branch=branch)
    except IntegrityError:raise HttpError(409,'Registration already exists')
    return {'id':v.pk,'registration':v.registration,'model_name':v.model_name,'branch_id':v.branch_id}

@router.patch('/orgs/{org_id}/parties/{party_id}')
def update_party(request,org_id:int,party_id:int,payload:PartyPatch):
    m=membership(request,org_id,'party.write',party=party_id)
    return party_dict(result(services.update_party,m.organization,party_id,payload.dict(),request.user))

@router.get('/orgs/{org_id}/parties/{party_id}/ids')
def get_ids(request,org_id:int,party_id:int):
    """NID and licence numbers: full for an administrator, last four characters for the finance roles, 403 for everyone else. Always logged."""
    m=membership(request,org_id,'party.ids_masked',party=party_id)
    return result(services.party_ids,m.organization,party_id,request.user,policies.can(m.role,'party.read_ids'))

@router.put('/orgs/{org_id}/parties/{party_id}/ids')
def put_ids(request,org_id:int,party_id:int,payload:IdsIn):
    m=membership(request,org_id,'party.read_ids',party=party_id)
    result(services.set_party_ids,m.organization,party_id,payload.dict(),request.user)
    return result(services.party_ids,m.organization,party_id,request.user,True)

@router.put('/orgs/{org_id}/parties/{party_id}/consent')
def set_consent(request,org_id:int,party_id:int,payload:ConsentIn):
    m=membership(request,org_id,'sensitive',party=party_id)
    return party_dict(result(services.set_consent,m.organization,party_id,**payload.dict(),actor=request.user),sensitive=True)

@router.post('/orgs/{org_id}/parties/{party_id}/contacts')
def add_contact(request,org_id:int,party_id:int,payload:ContactIn):
    m=membership(request,org_id,'party.write',party=party_id)
    c=result(services.add_contact,m.organization,party_id,payload.dict(),request.user)
    return {'id':c.pk,'name':c.name,'phone':c.phone,'email':c.email,'role':c.role}

@router.delete('/orgs/{org_id}/parties/{party_id}/contacts/{contact_id}')
def delete_contact(request,org_id:int,party_id:int,contact_id:int):
    m=membership(request,org_id,'party.write',party=party_id)
    result(services.delete_contact,m.organization,party_id,contact_id,request.user)
    return {'deleted':True}

GONE="Owner, driver and customer logins are retired. Staff open each party's profile page instead (milestone M10)."

@router.post('/orgs/{org_id}/parties/{party_id}/access')
def grant_access(request,org_id:int,party_id:int,payload:AccessIn):
    membership(request,org_id,'access.manage')
    raise HttpError(410,GONE)

@router.delete('/orgs/{org_id}/parties/{party_id}/access/{user_id}')
def revoke_access(request,org_id:int,party_id:int,user_id:int):
    membership(request,org_id,'access.manage')
    raise HttpError(410,GONE)

@router.get('/orgs/{org_id}/branches')
def branches(request,org_id:int):
    m=membership(request,org_id,'read_financial')
    qs=Branch.objects.filter(organization=m.organization)
    if policies.limited_branch(m):qs=qs.filter(pk=m.branch_id)
    return [{'id':b.pk,'name':b.name} for b in qs.order_by('name')]

@router.post('/orgs/{org_id}/branches')
def create_branch(request,org_id:int,payload:BranchIn):
    m=membership(request,org_id,'branch.write')
    b=result(services.create_branch,m.organization,payload.name,request.user)
    return {'id':b.pk,'name':b.name}

def member_dict(x):
    return {'id':x.pk,'user_id':x.user_id,'username':x.user.get_username(),'role':x.role,'branch':{'id':x.branch_id,'name':x.branch.name} if x.branch_id else None}

@router.get('/orgs/{org_id}/members')
def members(request,org_id:int):
    m=membership(request,org_id,'access.manage')
    rows=Membership.objects.filter(organization=m.organization,role__in=policies.STAFF_ROLES).select_related('user','branch').order_by('user__username')
    return [member_dict(x) for x in rows]

@router.patch('/orgs/{org_id}/members/{member_id}')
def update_member(request,org_id:int,member_id:int,payload:MemberPatch):
    m=membership(request,org_id,'access.manage')
    return member_dict(result(services.update_member,m.organization,request.user,member_id,payload.role,payload.branch_id,payload.clear_branch))
