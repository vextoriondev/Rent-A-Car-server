from ninja import Router
from ninja.errors import HttpError
from .. import policies
from ..models import NotificationOutbox
from ._shared import membership

router = Router()

@router.get('/orgs/{org_id}/notifications')
def notifications(request,org_id:int):
    m=membership(request,org_id,'read')
    return [{'id':n.pk,'event_type':n.event_type,'recipient':n.recipient.name,'state':n.state,'attempts':n.attempts,'created_at':n.created_at.isoformat()} for n in policies.scope_queryset(request.user,m,NotificationOutbox.objects.all()).select_related('recipient').order_by('-id')[:200]]

@router.post('/orgs/{org_id}/notifications/{notice_id}/retry')
def retry_notice(request,org_id:int,notice_id:int):
    membership(request,org_id,'notice.retry')
    notice=NotificationOutbox.objects.filter(pk=notice_id,organization_id=org_id).first()
    if not notice:raise HttpError(404,'Notice not found')
    if notice.state=='delivered':raise HttpError(422,'Delivered messages do not need retry')
    notice.state='queued';notice.last_error='';notice.save(update_fields=['state','last_error'])
    return {'id':notice.pk,'state':notice.state}
