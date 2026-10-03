from ninja import Router
from ninja.errors import HttpError
from .. import services
from ..models import Settlement
from ._shared import ReasonIn, SettingsIn, membership, result, settlement_dict, money_dict

router = Router()

@router.get('/orgs/{org_id}/settlement-settings')
def get_settings(request,org_id:int):
    m=membership(request,org_id,'read_financial')
    s=services.get_settings(m.organization)
    return {'cutoff_day':s.cutoff_day,'approval_role':s.approval_role,'negative_owner_blocks_close':s.negative_owner_blocks_close}

@router.put('/orgs/{org_id}/settlement-settings')
def put_settings(request,org_id:int,payload:SettingsIn):
    m=membership(request,org_id,'settings.write')
    s=result(services.update_settings,m.organization,request.user,**payload.dict())
    return {'cutoff_day':s.cutoff_day,'approval_role':s.approval_role,'negative_owner_blocks_close':s.negative_owner_blocks_close}

@router.get('/orgs/{org_id}/periods/{period_id}/settlement-preview')
def preview(request,org_id:int,period_id:int):
    m=membership(request,org_id,'read_financial')
    return money_dict(result(services.preview_settlement,m.organization,period_id))

@router.post('/orgs/{org_id}/periods/{period_id}/settlement')
def prepare(request,org_id:int,period_id:int):
    m=membership(request,org_id,'settlement.prepare')
    return settlement_dict(result(services.prepare_settlement,m.organization,request.user,period_id))

@router.get('/orgs/{org_id}/settlements')
def settlements(request,org_id:int,month:str|None=None):
    m=membership(request,org_id,'read_financial')
    qs=Settlement.objects.filter(organization=m.organization)
    if month:qs=qs.filter(month=month)
    return [settlement_dict(s) for s in qs.order_by('-month','-id')[:200]]

@router.get('/orgs/{org_id}/settlements/{settlement_id}')
def settlement(request,org_id:int,settlement_id:int):
    m=membership(request,org_id,'read_financial')
    s=Settlement.objects.filter(pk=settlement_id,organization=m.organization).first()
    if not s:raise HttpError(404,'Settlement not found')
    return settlement_dict(s,lines=True)

@router.post('/orgs/{org_id}/settlements/{settlement_id}/request-approval')
def request_approval(request,org_id:int,settlement_id:int):
    m=membership(request,org_id,'settlement.prepare')
    return settlement_dict(result(services.request_settlement_approval,m.organization,request.user,settlement_id),lines=True)

@router.post('/orgs/{org_id}/settlements/{settlement_id}/approve')
def approve(request,org_id:int,settlement_id:int):
    m=membership(request,org_id,'approve')
    return settlement_dict(result(services.approve_settlement,m.organization,request.user,settlement_id,m.role))

@router.post('/orgs/{org_id}/settlements/{settlement_id}/close')
def close(request,org_id:int,settlement_id:int):
    m=membership(request,org_id,'settle')
    return settlement_dict(result(services.close_settlement,m.organization,request.user,settlement_id),lines=True)

@router.post('/orgs/{org_id}/settlements/{settlement_id}/reopen')
def reopen(request,org_id:int,settlement_id:int,payload:ReasonIn):
    m=membership(request,org_id,'reopen')
    return settlement_dict(result(services.reopen_settlement,m.organization,request.user,settlement_id,payload.reason))
