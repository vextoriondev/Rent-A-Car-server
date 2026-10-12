from datetime import date
from django.http import HttpResponse
from ninja import Field, Query, Router, Schema
from ninja.errors import HttpError
from .. import policies, services
from ..models import Party
from ..services import portal as portal_service
from ._shared import document_dict, membership, money_dict, result
from .snapshot import notice_text

router = Router()

class RangeQuery(Schema):
    from_:date|None=Field(None,alias='from'); to:date|None=None

def open_party(request,org_id,party_id,section):
    """The staff member, the party (404 across organizations and branches) and an audit row: every view of a page is logged."""
    m=membership(request,org_id,'party.portal',party=party_id)
    party=policies.scope_queryset(request.user,m,Party.objects.filter(organization_id=org_id)).prefetch_related('contacts').filter(pk=party_id).first()
    if not party:raise HttpError(404,'Record not found')
    services.audit(m.organization,request.user,'sensitive.read','party_portal',party.pk,after={'section':section,'kind':party.kind})
    return m,party

@router.get('/orgs/{org_id}/parties/{party_id}/portal')
def portal(request,org_id:int,party_id:int):
    m,p=open_party(request,org_id,party_id,'profile')
    return money_dict(result(portal_service.portal_profile,m.organization,p))

@router.get('/orgs/{org_id}/parties/{party_id}/portal/statement')
def statement(request,org_id:int,party_id:int,q:RangeQuery=Query(...)):
    m,p=open_party(request,org_id,party_id,'statement')
    return money_dict(result(portal_service.portal_statement,m.organization,p,q.from_,q.to))

@router.get('/orgs/{org_id}/parties/{party_id}/portal/statement/export')
def statement_export(request,org_id:int,party_id:int,q:RangeQuery=Query(...)):
    m=membership(request,org_id,'export',party=party_id)
    party=policies.scope_queryset(request.user,m,Party.objects.filter(organization_id=org_id)).filter(pk=party_id).first()
    if not party:raise HttpError(404,'Record not found')
    text=result(portal_service.portal_statement_csv,m.organization,party,q.from_,q.to)
    services.audit(m.organization,request.user,'party.portal_export','party',party.pk,after={'from':q.from_,'to':q.to,'kind':party.kind})
    resp=HttpResponse(text,content_type='text/csv; charset=utf-8')
    resp['Content-Disposition']=f'attachment; filename="rentflow-{party.kind}-{party.pk}-statement.csv"'
    resp['Cache-Control']='private, no-store'
    return resp

@router.get('/orgs/{org_id}/parties/{party_id}/portal/obligations')
def obligations(request,org_id:int,party_id:int,limit:int=100,offset:int=0):
    m,p=open_party(request,org_id,party_id,'obligations')
    return money_dict(portal_service.portal_obligations(m.organization,p,limit,offset))

@router.get('/orgs/{org_id}/parties/{party_id}/portal/payments')
def payments(request,org_id:int,party_id:int,limit:int=100,offset:int=0):
    m,p=open_party(request,org_id,party_id,'payments')
    return money_dict(portal_service.portal_payments(m.organization,p,limit,offset))

@router.get('/orgs/{org_id}/parties/{party_id}/portal/deductions')
def deductions(request,org_id:int,party_id:int,limit:int=100,offset:int=0):
    m,p=open_party(request,org_id,party_id,'deductions')
    if p.kind!='owner':raise HttpError(404,'Deductions exist for owners only')
    return money_dict(portal_service.portal_deductions(m.organization,m,p,limit,offset))

@router.get('/orgs/{org_id}/parties/{party_id}/portal/activity')
def activity(request,org_id:int,party_id:int,limit:int=100,offset:int=0):
    m,p=open_party(request,org_id,party_id,'activity')
    return money_dict(portal_service.portal_activity(m.organization,p,limit,offset))

@router.get('/orgs/{org_id}/parties/{party_id}/portal/documents')
def documents(request,org_id:int,party_id:int):
    m,p=open_party(request,org_id,party_id,'documents')
    return [document_dict(d) for d in portal_service.portal_documents(m.organization,m,p)]

@router.get('/orgs/{org_id}/parties/{party_id}/portal/notices')
def notices(request,org_id:int,party_id:int,limit:int=100,offset:int=0):
    m,p=open_party(request,org_id,party_id,'notices')
    page=portal_service.portal_notices(m.organization,p,limit,offset)
    page['items']=[{'id':n.pk,'event_type':n.event_type,'title':n.event_type.replace('_',' ').title(),'text':notice_text(n),'state':n.state,'created_at':n.created_at.isoformat()} for n in page['items']]
    return page
