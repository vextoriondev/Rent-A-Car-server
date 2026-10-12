from ninja import Router
from ninja.errors import HttpError
from .. import policies, services
from ..models import Settlement
from ._shared import ReasonIn, SettingsIn, membership, result, settlement_dict, money_dict, paginate

router = Router()

@router.get('/orgs/{org_id}/settlement-settings')
def get_settings(request,org_id:int):
    m=membership(request,org_id,'read_financial')
    s=services.get_settings(m.organization)
    return {'cutoff_day':s.cutoff_day,'approval_role':s.approval_role,'negative_owner_blocks_close':s.negative_owner_blocks_close}

@router.get('/orgs/{org_id}/workspace')
def workspace(request,org_id:int):
    """The live, read-only policies of the workspace, so no screen has to hard-code them. `message_delivery` becomes `connected` when M7 sends real messages."""
    m=membership(request,org_id,'read')
    from ..services.expenses import DEFAULT_THRESHOLD
    s=services.get_settings(m.organization)
    return {'name':m.organization.name,'currency':'BDT','default_expense_threshold':str(DEFAULT_THRESHOLD),'month_end':{'cutoff_day':s.cutoff_day,'approval_role':s.approval_role},'message_delivery':'not_connected'}

@router.put('/orgs/{org_id}/settlement-settings')
def put_settings(request,org_id:int,payload:SettingsIn):
    m=membership(request,org_id,'settings.write')
    s=result(services.update_settings,m.organization,request.user,**payload.dict())
    return {'cutoff_day':s.cutoff_day,'approval_role':s.approval_role,'negative_owner_blocks_close':s.negative_owner_blocks_close}

@router.get('/orgs/{org_id}/periods/{period_id}/settlement-preview')
def preview(request,org_id:int,period_id:int):
    m=membership(request,org_id,'read_financial',period=period_id)
    return money_dict(result(services.preview_settlement,m.organization,period_id))

@router.post('/orgs/{org_id}/periods/{period_id}/settlement')
def prepare(request,org_id:int,period_id:int):
    m=membership(request,org_id,'settlement.prepare',period=period_id)
    return settlement_dict(result(services.prepare_settlement,m.organization,request.user,period_id))

@router.get('/orgs/{org_id}/settlements')
def settlements(request,org_id:int,month:str|None=None,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read_financial')
    qs=policies.branch_scope(m,Settlement.objects.filter(organization=m.organization),branch_id)
    if month:qs=qs.filter(month=month)
    return paginate(qs.order_by('-month','-id'),limit,offset,lambda s:settlement_dict(s))

@router.get('/orgs/{org_id}/settlements/{settlement_id}')
def settlement(request,org_id:int,settlement_id:int):
    m=membership(request,org_id,'read_financial',settlement=settlement_id)
    s=Settlement.objects.filter(pk=settlement_id,organization=m.organization).first()
    if not s:raise HttpError(404,'Settlement not found')
    return settlement_dict(s,lines=True)

@router.post('/orgs/{org_id}/settlements/{settlement_id}/request-approval')
def request_approval(request,org_id:int,settlement_id:int):
    m=membership(request,org_id,'settlement.prepare',settlement=settlement_id)
    return settlement_dict(result(services.request_settlement_approval,m.organization,request.user,settlement_id),lines=True)

@router.post('/orgs/{org_id}/settlements/{settlement_id}/approve')
def approve(request,org_id:int,settlement_id:int):
    m=membership(request,org_id,'approve',settlement=settlement_id)
    return settlement_dict(result(services.approve_settlement,m.organization,request.user,settlement_id,m.role))

@router.post('/orgs/{org_id}/settlements/{settlement_id}/close')
def close(request,org_id:int,settlement_id:int):
    m=membership(request,org_id,'settle',settlement=settlement_id)
    return settlement_dict(result(services.close_settlement,m.organization,request.user,settlement_id),lines=True)

@router.post('/orgs/{org_id}/settlements/{settlement_id}/reopen')
def reopen(request,org_id:int,settlement_id:int,payload:ReasonIn):
    m=membership(request,org_id,'reopen',settlement=settlement_id)
    return settlement_dict(result(services.reopen_settlement,m.organization,request.user,settlement_id,payload.reason))
