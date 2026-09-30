from django.db import IntegrityError
from ninja import Router
from ninja.errors import HttpError
from ..models import Party, Vehicle
from ._shared import PartyIn, VehicleIn, membership

router = Router()

@router.get('/orgs/{org_id}/parties')
def parties(request,org_id:int,kind:str|None=None):
    membership(request,org_id)
    qs=Party.objects.filter(organization_id=org_id)
    if kind:qs=qs.filter(kind=kind)
    return [{'id':p.pk,'kind':p.kind,'name':p.name,'phone':p.phone,'email':p.email} for p in qs.order_by('name')[:200]]

@router.post('/orgs/{org_id}/parties')
def create_party(request,org_id:int,payload:PartyIn):
    membership(request,org_id,('admin','accountant','operations'))
    if not payload.name.strip():raise HttpError(422,'Name is required')
    p=Party.objects.create(organization_id=org_id,**payload.dict())
    return {'id':p.pk,'kind':p.kind,'name':p.name}

@router.get('/orgs/{org_id}/vehicles')
def vehicles(request,org_id:int):
    membership(request,org_id)
    return [{'id':v.pk,'registration':v.registration,'model_name':v.model_name,'owner_id':v.owner_id,'owner':v.owner.name} for v in Vehicle.objects.filter(organization_id=org_id).select_related('owner').order_by('registration')[:200]]

@router.post('/orgs/{org_id}/vehicles')
def create_vehicle(request,org_id:int,payload:VehicleIn):
    membership(request,org_id,('admin','operations'))
    owner=Party.objects.filter(pk=payload.owner_id,organization_id=org_id,kind=Party.Kind.OWNER).first()
    if not owner:raise HttpError(422,'Owner is invalid')
    try:v=Vehicle.objects.create(organization_id=org_id,registration=payload.registration.strip(),model_name=payload.model_name.strip(),owner=owner)
    except IntegrityError:raise HttpError(409,'Registration already exists')
    return {'id':v.pk,'registration':v.registration,'model_name':v.model_name}
