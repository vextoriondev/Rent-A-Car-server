from datetime import date
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from typing import Literal
from ninja import Query, Router, Schema
from ninja.errors import HttpError
from ..models import Party, Vehicle, Agreement, Period
from .. import policies, services
from ..models import Agreement as AgreementModel
from ._shared import DatesIn, AgreementIn, QuickAgreementIn, ReviseIn, AssignDriverIn, TransferOwnerIn, DraftIn, membership, pick_branch, result, agreement_dict, agreement_detail, version_dict, money_dict, paginate

router = Router()

def create_checked(request,org_id,agreement):
    """All creation paths go through services.create_agreement (same overlap validation); a vehicle clash stays HTTP 409."""
    m=membership(request,org_id,'agreement.write')
    try:return services.create_agreement(m.organization,request.user,agreement)
    except services.DomainError as exc:
        raise HttpError(409 if 'overlapping' in str(exc) else 422,str(exc)) from exc

@router.get('/orgs/{org_id}/agreements')
def agreements(request,org_id:int,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read')
    qs=policies.scope_queryset(request.user,m,Agreement.objects.all(),branch_id).select_related('vehicle__owner','customer','driver').order_by('-id')
    return paginate(qs,limit,offset,lambda a:policies.redact_agreement(agreement_dict(a),m.role))

@router.post('/orgs/{org_id}/agreements')
def create_agreement(request,org_id:int,payload:AgreementIn):
    membership(request,org_id,'agreement.write',vehicle=payload.vehicle_id)
    vehicle=Vehicle.objects.filter(pk=payload.vehicle_id,organization_id=org_id).select_related('owner').first()
    customer=Party.objects.filter(pk=payload.customer_id,organization_id=org_id,kind=Party.Kind.CUSTOMER).first()
    driver=Party.objects.filter(pk=payload.driver_id,organization_id=org_id,kind=Party.Kind.DRIVER).first() if payload.driver_id else None
    if not all([vehicle,customer]) or (payload.driver_id and not driver):raise HttpError(422,'Vehicle, customer or driver is invalid')
    a,warnings=create_checked(request,org_id,Agreement(organization_id=org_id,**payload.dict()))
    return {**agreement_dict(Agreement.objects.select_related('vehicle__owner','customer','driver').get(pk=a.pk)),'warnings':warnings}

@router.post('/orgs/{org_id}/agreements/quick')
def quick_agreement(request,org_id:int,payload:QuickAgreementIn):
    m=membership(request,org_id,'agreement.write')
    if not all(x.strip() for x in [payload.customer,payload.owner,payload.vehicle,payload.registration]) or (payload.driver_provider!='none' and not payload.driver.strip()):raise HttpError(422,'All parties and vehicle details are required')
    with transaction.atomic():
        if Vehicle.objects.filter(organization_id=org_id,registration__iexact=payload.registration.strip()).exists():raise HttpError(409,'Registration already exists; use the existing vehicle in the full agreement API')
        owner=Party.objects.create(organization_id=org_id,kind='owner',name=payload.owner.strip(),created_by=request.user)
        customer=Party.objects.create(organization_id=org_id,kind='customer',name=payload.customer.strip(),created_by=request.user)
        driver=Party.objects.create(organization_id=org_id,kind='driver',name=payload.driver.strip(),created_by=request.user) if payload.driver_provider!='none' else None
        vehicle=Vehicle.objects.create(organization_id=org_id,registration=payload.registration.strip(),model_name=payload.vehicle.strip(),owner=owner,branch=pick_branch(m))
        a=Agreement(organization_id=org_id,vehicle=vehicle,customer=customer,driver=driver,start_date=payload.start,end_date=payload.end,customer_rent_basis=payload.rent_basis,owner_rent_basis=payload.owner_rent_basis,driver_salary_basis=payload.salary_basis,payment_schedule=payload.payment_schedule or {},customer_rent=result(services.bd,payload.rent),owner_rent=result(services.bd,payload.owner_rent),driver_salary=Decimal('0.00') if payload.salary==0 else result(services.bd,payload.salary),fuel_responsible=payload.fuel.lower(),repair_responsible=payload.repair.lower(),owner_offset_allowed=payload.offset,driver_provider=payload.driver_provider,driver_paid_by=payload.driver_paid_by,clauses=payload.clauses.strip())
        a,warnings=create_checked(request,org_id,a)
    return {**agreement_dict(Agreement.objects.select_related('vehicle__owner','customer','driver').get(pk=a.pk)),'warnings':warnings}

@router.post('/orgs/{org_id}/agreements/{agreement_id}/periods')
def issue_period(request,org_id:int,agreement_id:int,month:date):
    m=membership(request,org_id,'period.issue',agreement=agreement_id)
    p=result(services.issue_period,m.organization,agreement_id,month,request.user)
    return {'period_id':p.pk,'month':p.month.isoformat(),'obligations':[{'id':o.pk,'kind':o.kind,'party_id':o.party_id,'amount':str(o.amount)} for o in p.obligations.all()]}

@router.get('/orgs/{org_id}/periods')
def periods(request,org_id:int,month:date|None=None,from_:date|None=Query(None,alias='from'),to:date|None=None,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read')
    qs=policies.scope_queryset(request.user,m,Period.objects.all(),branch_id)
    if month:qs=qs.filter(month=month)
    start,end=result(services.resolve_range,from_,to)
    qs=services.overlapping_periods(qs,start,end)  # every period that overlaps the range, not prorated
    return paginate(qs.order_by('-month','agreement_id'),limit,offset,lambda p:{'id':p.pk,'agreement_id':p.agreement_id,'driver_agreement_id':p.driver_agreement_id,'month':p.month.isoformat(),'closed':bool(p.closed_at)})

@router.get('/orgs/{org_id}/periods/{period_id}/statement')
def statement(request,org_id:int,period_id:int):
    membership(request,org_id,'read_financial',period=period_id)
    p=Period.objects.filter(pk=period_id,organization_id=org_id).first()
    if not p:raise HttpError(404,'Period not found')
    return money_dict(result(services.statement,p))

@router.post('/orgs/{org_id}/periods/{period_id}/close')
def close_period(request,org_id:int,period_id:int):
    m=membership(request,org_id,'period.close',period=period_id)
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
    m=membership(request,org_id,'agreement.write',agreement=agreement_id)
    a,warnings=result(services.activate_agreement,m.organization,request.user,agreement_id)
    return {'id':a.pk,'status':a.status,'warnings':warnings}

@router.post('/orgs/{org_id}/agreements/{agreement_id}/revise')
def revise(request,org_id:int,agreement_id:int,payload:ReviseIn):
    m=membership(request,org_id,'agreement.revise',agreement=agreement_id)
    v=result(services.revise_agreement,m.organization,request.user,agreement_id,payload.effective_from,payload.changes.dict(exclude_none=True),payload.reason)
    return version_dict(v)

@router.post('/orgs/{org_id}/agreements/{agreement_id}/assign-driver')
def assign_driver(request,org_id:int,agreement_id:int,payload:AssignDriverIn):
    m=membership(request,org_id,'driver.assign',agreement=agreement_id)
    a,warnings=result(services.assign_driver,m.organization,request.user,agreement_id,payload.driver_id,payload.effective_from,payload.salary_override)
    return {'driver_id':a.driver_id,'effective_from':a.effective_from.isoformat(),'warnings':warnings}

@router.post('/orgs/{org_id}/vehicles/{vehicle_id}/transfer-ownership')
def transfer_ownership(request,org_id:int,vehicle_id:int,payload:TransferOwnerIn):
    m=membership(request,org_id,'ownership.transfer',vehicle=vehicle_id)
    o=result(services.transfer_vehicle_ownership,m.organization,request.user,vehicle_id,payload.owner_id,payload.effective_from)
    return {'vehicle_id':o.vehicle_id,'owner_id':o.owner_id,'effective_from':o.effective_from.isoformat()}

@router.patch('/orgs/{org_id}/agreements/{agreement_id}/dates')
def update_dates(request,org_id:int,agreement_id:int,payload:DatesIn):
    """Extend, shorten or remove the end date (the start date never moves)."""
    m=membership(request,org_id,'agreement.revise',agreement=agreement_id)
    a=result(services.update_agreement_dates,m.organization,request.user,agreement_id,payload.dict())
    return {'id':a.pk,'start_date':a.start_date.isoformat(),'end_date':a.end_date.isoformat() if a.end_date else None}

class PreviewIn(Schema):
    start:date; end:date|None=None; rent:Decimal; owner_rent:Decimal; salary:Decimal=Decimal('0')
    rent_basis:Literal['per_day','per_month','fixed_total']='per_month'; owner_rent_basis:Literal['per_day','per_month','fixed_total']='per_month'; salary_basis:Literal['per_day','per_month','fixed_total']='per_month'
    payment_schedule:dict|None=None; has_driver:bool=True

@router.post('/orgs/{org_id}/agreement-preview')
def preview_agreement(request,org_id:int,payload:PreviewIn):
    """The wizard's summary: total days, the first months' amounts and the first due dates for a set of terms. Saves nothing."""
    membership(request,org_id,'agreement.write')
    out=result(services.preview_terms,payload.start,payload.end,{'customer':payload.rent,'owner':payload.owner_rent,'driver':payload.salary},{'customer':payload.rent_basis,'owner':payload.owner_rent_basis,'driver':payload.salary_basis},payload.payment_schedule,payload.has_driver)
    return money_dict(out)

@router.get('/orgs/{org_id}/agreements/{agreement_id}/dues')
def agreement_dues(request,org_id:int,agreement_id:int,side:str='customer',until:date|None=None):
    """When each side's money is due, what is paid (applied oldest first) and which due is next. Computed from the schedule and the months billed."""
    m=membership(request,org_id,'read_financial',agreement=agreement_id)
    a=policies.scope_queryset(request.user,m,Agreement.objects.all()).filter(pk=agreement_id).first()
    if not a:raise HttpError(404,'Record not found')
    items=result(services.dues_for,a,side,None,until)
    v=a.versions.exclude(status='draft').order_by('-effective_from','-number').first()
    schedule=(v.payment_schedule if v else {}) or {}
    row=lambda x:{**{k:(str(val) if not isinstance(val,(date,str)) else (val.isoformat() if isinstance(val,date) else val)) for k,val in x.items()}}
    nxt=services.next_due(items)
    return {'side':side,'schedule':schedule.get(side) or {'kind':'monthly','day':'last'},'items':[row(x) for x in items],'next_due':row(nxt) if nxt else None}

@router.get('/orgs/{org_id}/agreements/{agreement_id}/statement')
def agreement_statement(request,org_id:int,agreement_id:int,from_:date|None=Query(None,alias='from'),to:date|None=None):
    """Exact date range: per side the opening balance (before `from`), the movements and the closing balance (up to `to`)."""
    m=membership(request,org_id,'read_financial',agreement=agreement_id)
    a=policies.scope_queryset(request.user,m,Agreement.objects.all()).filter(pk=agreement_id).first()
    if not a:raise HttpError(404,'Record not found')
    return money_dict(result(services.agreement_range_statement,a,from_,to))
