from datetime import date
from decimal import Decimal
from ninja import Router, Schema
from ninja.errors import HttpError
from .errors import ApiError
from .. import policies, services
from ..models import DriverAgreement, Party, Vehicle
from ._shared import membership, pick_branch, result, paginate

router = Router()

class DriverAgreementIn(Schema):
    driver_id:int; start_date:date; salary:Decimal; end_date:date|None=None; vehicle_id:int|None=None; terms:str=''; branch_id:int|None=None
class DriverAgreementPatch(Schema): end_date:date|None=None; terms:str|None=None
class DriverReviseIn(Schema): effective_from:date; reason:str; salary:Decimal|None=None; terms:str|None=None

def driver_agreement_dict(da):
    return {'id':da.pk,'ref':f'DRV-{da.pk}','driver':{'id':da.driver_id,'name':da.driver.name},'vehicle':{'id':da.vehicle_id,'registration':da.vehicle.registration,'model_name':da.vehicle.model_name} if da.vehicle_id else None,
        'branch_id':da.branch_id,'start_date':da.start_date.isoformat(),'end_date':da.end_date.isoformat() if da.end_date else None,'salary':str(da.salary),'terms':da.terms,'status':da.status}

def detail(da):
    stmts={p.pk:services.statement(p)['driver'] for p in da.periods.order_by('month')}
    d=driver_agreement_dict(da)
    d['versions']=[{'number':v.number,'effective_from':v.effective_from.isoformat(),'salary':str(v.salary),'terms':v.terms,'reason':v.reason} for v in da.versions.order_by('number')]
    d['periods']=[{'id':p.pk,'month':p.month.isoformat(),'closed':bool(p.closed_at),'earned':str(stmts[p.pk]['earned']),'paid':str(stmts[p.pk]['paid']),'due':str(stmts[p.pk]['due']),'bill_state':stmts[p.pk]['bill_state']} for p in da.periods.order_by('month')]
    d['warnings']=services.check_driver_overlaps(da)
    return d

def fetch(request,m,da_id):
    da=policies.scope_queryset(request.user,m,DriverAgreement.objects.all()).select_related('driver','vehicle').filter(pk=da_id,organization_id=m.organization_id).first()
    if not da:raise ApiError(404, 'record_not_found')
    return da

@router.get('/orgs/{org_id}/driver-agreements')
def driver_agreements(request,org_id:int,driver_id:int|None=None,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read_financial')
    qs=policies.scope_queryset(request.user,m,DriverAgreement.objects.filter(organization_id=org_id),branch_id).select_related('driver','vehicle')
    if driver_id:qs=qs.filter(driver_id=driver_id)
    return paginate(qs.order_by('-id'),limit,offset,driver_agreement_dict)

@router.post('/orgs/{org_id}/driver-agreements')
def create_driver_agreement(request,org_id:int,payload:DriverAgreementIn):
    m=membership(request,org_id,'agreement.write')
    driver=Party.objects.filter(pk=payload.driver_id,organization_id=org_id,kind=Party.Kind.DRIVER).first()
    vehicle=Vehicle.objects.filter(pk=payload.vehicle_id,organization_id=org_id).first() if payload.vehicle_id else None
    if not driver or (payload.vehicle_id and not vehicle):raise ApiError(422, 'driver_or_vehicle_is_invalid')
    if vehicle and not policies.branch_scope(m,Vehicle.objects.filter(pk=vehicle.pk)).exists():raise ApiError(404, 'record_not_found')
    da=DriverAgreement(organization_id=org_id,driver=driver,vehicle=vehicle,start_date=payload.start_date,end_date=payload.end_date,salary=payload.salary,terms=payload.terms.strip(),branch=vehicle.branch if vehicle else pick_branch(m,payload.branch_id))
    da,warnings=result(services.create_driver_agreement,m.organization,request.user,da)
    return {**driver_agreement_dict(DriverAgreement.objects.select_related('driver','vehicle').get(pk=da.pk)),'warnings':warnings}

@router.get('/orgs/{org_id}/driver-agreements/{da_id}')
def driver_agreement_detail(request,org_id:int,da_id:int):
    m=membership(request,org_id,'read_financial')
    return detail(fetch(request,m,da_id))

@router.patch('/orgs/{org_id}/driver-agreements/{da_id}')
def update_driver_agreement(request,org_id:int,da_id:int,payload:DriverAgreementPatch):
    m=membership(request,org_id,'agreement.write')
    fetch(request,m,da_id)
    da=result(services.update_driver_agreement,m.organization,request.user,da_id,payload.dict(exclude_unset=True))
    return driver_agreement_dict(DriverAgreement.objects.select_related('driver','vehicle').get(pk=da.pk))

@router.post('/orgs/{org_id}/driver-agreements/{da_id}/revise')
def revise_driver_agreement(request,org_id:int,da_id:int,payload:DriverReviseIn):
    m=membership(request,org_id,'agreement.revise')
    fetch(request,m,da_id)
    changes={k:v for k,v in (('salary',payload.salary),('terms',payload.terms)) if v is not None}
    v=result(services.revise_driver_agreement,m.organization,request.user,da_id,payload.effective_from,changes,payload.reason)
    return {'number':v.number,'effective_from':v.effective_from.isoformat(),'salary':str(v.salary),'terms':v.terms,'reason':v.reason}

@router.post('/orgs/{org_id}/driver-agreements/{da_id}/periods')
def issue_driver_period(request,org_id:int,da_id:int,month:date):
    m=membership(request,org_id,'period.issue')
    fetch(request,m,da_id)
    p=result(services.issue_driver_period,m.organization,da_id,month,request.user)
    s=services.statement(p)['driver']
    return {'id':p.pk,'driver_agreement_id':da_id,'month':p.month.isoformat(),'closed':bool(p.closed_at),'earned':str(s['earned']),'due':str(s['due']),'bill_state':s['bill_state']}

@router.get('/orgs/{org_id}/parties/{party_id}/driver-balance')
def driver_balance(request,org_id:int,party_id:int):
    """What the operator owes one driver, split between vehicle agreements and standalone driver agreements."""
    m=membership(request,org_id,'read_financial',party=party_id)
    split=result(services.driver_balance_split,m.organization,party_id)
    return {k:({'due':str(v['due']),'items':[{**i,'due':str(i['due'])} for i in v['items']]} if isinstance(v,dict) else str(v)) for k,v in split.items()}
