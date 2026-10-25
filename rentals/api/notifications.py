from ninja import Router
from .errors import ApiError
from .. import policies, services
from ..models import NotificationOutbox
from ._shared import membership, paginate

router = Router()

def delivery_dict(d):
    return {'id':d.pk,'channel':d.channel,'is_admin_copy':d.is_admin_copy,'to':d.to_address,'state':d.state,'attempts':d.attempts,'provider':d.provider,'error':d.last_error,'sent_at':d.sent_at.isoformat() if d.sent_at else None}

def notice_dict(n):
    return {'id':n.pk,'event_type':n.event_type,'recipient':n.recipient.name,'state':n.state,'attempts':n.attempts,'created_at':n.created_at.isoformat(),'error':n.last_error,
        'deliveries':[delivery_dict(d) for d in n.deliveries.all()]}

@router.get('/orgs/{org_id}/notifications')
def notifications(request,org_id:int,branch_id:int|None=None,state:str|None=None,channel:str|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read')
    qs=policies.scope_queryset(request.user,m,NotificationOutbox.objects.all(),branch_id).select_related('recipient').prefetch_related('deliveries').order_by('-id')
    if state:qs=qs.filter(state=state)
    if channel:qs=qs.filter(deliveries__channel=channel).distinct()
    return paginate(qs,limit,offset,notice_dict)

@router.post('/orgs/{org_id}/notifications/{notice_id}/retry')
def retry_notice(request,org_id:int,notice_id:int):
    membership(request,org_id,'notice.retry',notice=notice_id)
    notice=NotificationOutbox.objects.filter(pk=notice_id,organization_id=org_id).first()
    if not notice:raise ApiError(404, 'notice_not_found')
    if notice.state in ('sent','delivered'):raise ApiError(422, 'delivered_messages_do_not_need_retry')
    if notice.deliveries.exists():services.requeue_notice(notice)
    else:   # a row from before M7 has no deliveries: plan them now
        from ..services import notify
        notify.dispatch(notify.plan(notice))
    notice.refresh_from_db()
    return {'id':notice.pk,'state':notice.state}
