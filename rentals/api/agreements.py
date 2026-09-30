from datetime import date
from django.db import transaction
from django.db.models import Q
from ninja import Router
from ninja.errors import HttpError
from ..models import Party, Vehicle, Agreement, Period
from .. import services
from ._shared import AgreementIn, QuickAgreementIn, membership, result, agreement_dict, money_dict

router = Router()

@router.get('/orgs/{org_id}/agreements')
def agreements(request,org_id:int,limit:int=100,offset:int=0):
    membership(request,org_id)
    qs=Agreement.objects.filter(organization_id=org_id).select_related('vehicle__owner','customer','driver').order_by('-id')
    return {'count':qs.count(),'items':[agreement_dict(a) for a in qs[max(0,offset):max(0,offset)+min(100,max(1,limit))]]}

@router.post('/orgs/{org_id}/agreements')
def create_agreement(request,org_id:int,payload:AgreementIn):
    membership(request,org_id,('admin','accountant','operations'))
    vehicle=Vehicle.objects.filter(pk=payload.vehicle_id,organization_id=org_id).select_related('owner').first()
    customer=Party.objects.filter(pk=payload.customer_id,organization_id=org_id,kind=Party.Kind.CUSTOMER).first()
    driver=Party.objects.filter(pk=payload.driver_id,organization_id=org_id,kind=Party.Kind.DRIVER).first()
    if not all([vehicle,customer,driver]):raise HttpError(422,'Vehicle, customer or driver is invalid')
    with transaction.atomic():
        if Agreement.objects.select_for_update().filter(vehicle=vehicle,active=True).filter(Q(end_date__isnull=True)|Q(end_date__gte=payload.start_date)).exists():raise HttpError(409,'Vehicle has an overlapping active agreement')
        a=Agreement(organization_id=org_id,**payload.dict())
        result(services.validate_agreement,a)
        a.save()
    return agreement_dict(Agreement.objects.select_related('vehicle__owner','customer','driver').get(pk=a.pk))

@router.post('/orgs/{org_id}/agreements/quick')
def quick_agreement(request,org_id:int,payload:QuickAgreementIn):
    membership(request,org_id,('admin','accountant','operations'))
    if not all(x.strip() for x in [payload.customer,payload.owner,payload.driver,payload.vehicle,payload.registration]):raise HttpError(422,'All parties and vehicle details are required')
    with transaction.atomic():
        if Vehicle.objects.filter(organization_id=org_id,registration__iexact=payload.registration.strip()).exists():raise HttpError(409,'Registration already exists; use the existing vehicle in the full agreement API')
        owner=Party.objects.create(organization_id=org_id,kind='owner',name=payload.owner.strip())
        customer=Party.objects.create(organization_id=org_id,kind='customer',name=payload.customer.strip())
        driver=Party.objects.create(organization_id=org_id,kind='driver',name=payload.driver.strip())
        vehicle=Vehicle.objects.create(organization_id=org_id,registration=payload.registration.strip(),model_name=payload.vehicle.strip(),owner=owner)
        a=Agreement(organization_id=org_id,vehicle=vehicle,customer=customer,driver=driver,start_date=payload.start,customer_rent=result(services.bd,payload.rent),owner_rent=result(services.bd,payload.owner_rent),driver_salary=result(services.bd,payload.salary),fuel_responsible=payload.fuel.lower(),repair_responsible=payload.repair.lower(),owner_offset_allowed=payload.offset)
        result(services.validate_agreement,a)
        a.save()
    return agreement_dict(Agreement.objects.select_related('vehicle__owner','customer','driver').get(pk=a.pk))

@router.post('/orgs/{org_id}/agreements/{agreement_id}/periods')
def issue_period(request,org_id:int,agreement_id:int,month:date):
    membership(request,org_id,('admin','accountant'))
    p=result(services.issue_period,membership(request,org_id).organization,agreement_id,month)
    return {'period_id':p.pk,'month':p.month.isoformat(),'obligations':[{'id':o.pk,'kind':o.kind,'party_id':o.party_id,'amount':str(o.amount)} for o in p.obligations.all()]}

@router.get('/orgs/{org_id}/periods')
def periods(request,org_id:int,month:date|None=None):
    membership(request,org_id)
    qs=Period.objects.filter(organization_id=org_id)
    if month:qs=qs.filter(month=month)
    return [{'id':p.pk,'agreement_id':p.agreement_id,'month':p.month.isoformat(),'closed':bool(p.closed_at)} for p in qs.order_by('-month','agreement_id')[:200]]

@router.get('/orgs/{org_id}/periods/{period_id}/statement')
def statement(request,org_id:int,period_id:int):
    membership(request,org_id)
    p=Period.objects.filter(pk=period_id,organization_id=org_id).first()
    if not p:raise HttpError(404,'Period not found')
    return money_dict(result(services.statement,p))

@router.post('/orgs/{org_id}/periods/{period_id}/close')
def close_period(request,org_id:int,period_id:int):
    m=membership(request,org_id,('admin','accountant'))
    p=result(services.close_period,m.organization,request.user,period_id)
    return {'period_id':p.pk,'closed':True,'closed_at':p.closed_at.isoformat()}
