from datetime import date
from django.db import transaction
from django.db.models import Q
from ninja import Router
from ninja.errors import HttpError
from ..models import Party, Vehicle, Agreement, Period
from .. import policies, services
from ..models import Agreement as AgreementModel
from ._shared import AgreementIn, QuickAgreementIn, ReviseIn, AssignDriverIn, TransferOwnerIn, DraftIn, membership, result, agreement_dict, agreement_detail, version_dict, money_dict, paginate

router = Router()

def create_checked(request,org_id,agreement):
    """All creation paths go through services.create_agreement (same overlap validation); a vehicle clash stays HTTP 409."""
    m=membership(request,org_id,'agreement.write')
    try:return services.create_agreement(m.organization,request.user,agreement)
    except services.DomainError as exc:
        raise HttpError(409 if 'overlapping' in str(exc) else 422,str(exc)) from exc

@router.get('/orgs/{org_id}/agreements')
def agreements(request,org_id:int,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read')
    qs=policies.scope_queryset(request.user,m,Agreement.objects.all()).select_related('vehicle__owner','customer','driver').order_by('-id')
    return paginate(qs,limit,offset,lambda a:policies.redact_agreement(agreement_dict(a),m.role))

@router.post('/orgs/{org_id}/agreements')
def create_agreement(request,org_id:int,payload:AgreementIn):
    membership(request,org_id,'agreement.write')
    vehicle=Vehicle.objects.filter(pk=payload.vehicle_id,organization_id=org_id).select_related('owner').first()
    customer=Party.objects.filter(pk=payload.customer_id,organization_id=org_id,kind=Party.Kind.CUSTOMER).first()
    driver=Party.objects.filter(pk=payload.driver_id,organization_id=org_id,kind=Party.Kind.DRIVER).first()
    if not all([vehicle,customer,driver]):raise HttpError(422,'Vehicle, customer or driver is invalid')
    a,warnings=create_checked(request,org_id,Agreement(organization_id=org_id,**payload.dict()))
    return {**agreement_dict(Agreement.objects.select_related('vehicle__owner','customer','driver').get(pk=a.pk)),'warnings':warnings}

@router.post('/orgs/{org_id}/agreements/quick')
def quick_agreement(request,org_id:int,payload:QuickAgreementIn):
    membership(request,org_id,'agreement.write')
    if not all(x.strip() for x in [payload.customer,payload.owner,payload.driver,payload.vehicle,payload.registration]):raise HttpError(422,'All parties and vehicle details are required')
    with transaction.atomic():
        if Vehicle.objects.filter(organization_id=org_id,registration__iexact=payload.registration.strip()).exists():raise HttpError(409,'Registration already exists; use the existing vehicle in the full agreement API')
        owner=Party.objects.create(organization_id=org_id,kind='owner',name=payload.owner.strip())
        customer=Party.objects.create(organization_id=org_id,kind='customer',name=payload.customer.strip())
        driver=Party.objects.create(organization_id=org_id,kind='driver',name=payload.driver.strip())
        vehicle=Vehicle.objects.create(organization_id=org_id,registration=payload.registration.strip(),model_name=payload.vehicle.strip(),owner=owner)
        a=Agreement(organization_id=org_id,vehicle=vehicle,customer=customer,driver=driver,start_date=payload.start,customer_rent=result(services.bd,payload.rent),owner_rent=result(services.bd,payload.owner_rent),driver_salary=result(services.bd,payload.salary),fuel_responsible=payload.fuel.lower(),repair_responsible=payload.repair.lower(),owner_offset_allowed=payload.offset)
        a,warnings=create_checked(request,org_id,a)
    return {**agreement_dict(Agreement.objects.select_related('vehicle__owner','customer','driver').get(pk=a.pk)),'warnings':warnings}

@router.post('/orgs/{org_id}/agreements/{agreement_id}/periods')
def issue_period(request,org_id:int,agreement_id:int,month:date):
    m=membership(request,org_id,'period.issue')
    p=result(services.issue_period,m.organization,agreement_id,month,request.user)
    return {'period_id':p.pk,'month':p.month.isoformat(),'obligations':[{'id':o.pk,'kind':o.kind,'party_id':o.party_id,'amount':str(o.amount)} for o in p.obligations.all()]}

@router.get('/orgs/{org_id}/periods')
def periods(request,org_id:int,month:date|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read')
    qs=policies.scope_queryset(request.user,m,Period.objects.all())
    if month:qs=qs.filter(month=month)
    return paginate(qs.order_by('-month','agreement_id'),limit,offset,lambda p:{'id':p.pk,'agreement_id':p.agreement_id,'month':p.month.isoformat(),'closed':bool(p.closed_at)})

@router.get('/orgs/{org_id}/periods/{period_id}/statement')
def statement(request,org_id:int,period_id:int):
    membership(request,org_id,'read_financial')
    p=Period.objects.filter(pk=period_id,organization_id=org_id).first()
    if not p:raise HttpError(404,'Period not found')
    return money_dict(result(services.statement,p))

@router.post('/orgs/{org_id}/periods/{period_id}/close')
def close_period(request,org_id:int,period_id:int):
    m=membership(request,org_id,'period.close')
    p=result(services.close_period,m.organization,request.user,period_id)
    return {'period_id':p.pk,'closed':True,'closed_at':p.closed_at.isoformat()}

@router.get('/orgs/{org_id}/agreements/{agreement_id}')
def agreement_detail_view(request,org_id:int,agreement_id:int):
    m=membership(request,org_id,'read')
    a=policies.scope_queryset(request.user,m,Agreement.objects.all()).select_related('vehicle__owner','customer','driver').filter(pk=agreement_id).first()
    if not a:raise HttpError(404,'Record not found')
    services.audit(m.organization,request.user,'sensitive.read','agreement',a.pk)
    if policies.is_actor(m.role):return policies.redact_agreement(agreement_dict(a),m.role)
    return agreement_detail(a)

@router.post('/orgs/{org_id}/agreements/{agreement_id}/activate')
def activate(request,org_id:int,agreement_id:int):
    m=membership(request,org_id,'agreement.write')
    a,warnings=result(services.activate_agreement,m.organization,request.user,agreement_id)
    return {'id':a.pk,'status':a.status,'warnings':warnings}

@router.post('/orgs/{org_id}/agreements/{agreement_id}/revise')
def revise(request,org_id:int,agreement_id:int,payload:ReviseIn):
    m=membership(request,org_id,'agreement.revise')
    v=result(services.revise_agreement,m.organization,request.user,agreement_id,payload.effective_from,payload.changes.dict(exclude_none=True),payload.reason)
    return version_dict(v)

@router.post('/orgs/{org_id}/agreements/{agreement_id}/assign-driver')
def assign_driver(request,org_id:int,agreement_id:int,payload:AssignDriverIn):
    m=membership(request,org_id,'driver.assign')
    a,warnings=result(services.assign_driver,m.organization,request.user,agreement_id,payload.driver_id,payload.effective_from,payload.salary_override)
    return {'driver_id':a.driver_id,'effective_from':a.effective_from.isoformat(),'warnings':warnings}

@router.post('/orgs/{org_id}/vehicles/{vehicle_id}/transfer-ownership')
def transfer_ownership(request,org_id:int,vehicle_id:int,payload:TransferOwnerIn):
    m=membership(request,org_id,'ownership.transfer')
    o=result(services.transfer_vehicle_ownership,m.organization,request.user,vehicle_id,payload.owner_id,payload.effective_from)
    return {'vehicle_id':o.vehicle_id,'owner_id':o.owner_id,'effective_from':o.effective_from.isoformat()}
