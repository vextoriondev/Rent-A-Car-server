from datetime import date, datetime, time, timezone
from django.http import FileResponse, HttpResponse
from ninja import File, Form, Router, UploadedFile
from ninja.errors import HttpError
from .. import policies, services
from ..models import AuditEvent, Document, MaintenanceCase
from ._shared import CaseIn, CasePatch, membership, result, case_dict, document_dict, audit_dict, paginate
import io

router = Router()

# ---- documents
@router.post('/orgs/{org_id}/documents')
def upload(request,org_id:int,target_type:Form[str],target_id:Form[int],file:File[UploadedFile],category:Form[str]=''):
    m=membership(request,org_id,'document.upload')
    if not services.in_branch(m,target_type,target_id):raise HttpError(404,'Record not found')
    doc=result(services.upload_document,m.organization,request.user,target_type,target_id,file.name,file.content_type,file.read(),m.role,category)
    return document_dict(doc)

@router.get('/orgs/{org_id}/documents')
def documents(request,org_id:int,target_type:str,target_id:int):
    m=membership(request,org_id,'document.read')
    if not services.in_branch(m,target_type,target_id):raise HttpError(404,'Record not found')
    return [document_dict(d) for d in services.list_documents(m.organization,m,target_type,target_id)]

@router.get('/orgs/{org_id}/documents/{document_id}/url')
def document_url(request,org_id:int,document_id:int):
    m=membership(request,org_id,'document.read')
    return result(services.issue_url,m.organization,m,request.user,document_id)

@router.delete('/orgs/{org_id}/documents/{document_id}')
def delete_document(request,org_id:int,document_id:int):
    m=membership(request,org_id,'document.delete')
    doc=Document.objects.filter(pk=document_id,organization=m.organization).first()
    if doc and not services.in_branch(m,doc.target_type,doc.target_id):raise HttpError(404,'Record not found')
    result(services.delete_document,m.organization,request.user,document_id)
    return {'deleted':True}

@router.get('/documents/{document_id}/download')
def download(request,document_id:int,token:str):
    try:doc=services.resolve_token(token,request.user)
    except services.DomainError as exc:raise HttpError(403,str(exc)) from exc
    if doc.pk!=document_id:raise HttpError(403,'Invalid link')
    member=policies_member(request,doc.organization_id)
    if not services.can_access(member,doc):raise HttpError(403,'You cannot open this document')
    try:data=services.read_bytes(doc)
    except services.DomainError as exc:raise HttpError(500,str(exc)) from exc
    resp=FileResponse(io.BytesIO(data),content_type=doc.content_type,as_attachment=True,filename=doc.original_name)
    resp['X-Content-Type-Options']='nosniff';resp['Cache-Control']='private, no-store'
    return resp

def policies_member(request,org_id):
    from ..models import Membership
    m=Membership.objects.filter(organization_id=org_id,user=request.user).first()
    if not m:raise HttpError(403,'You cannot open this document')
    return m

# ---- maintenance
def _case_qs(request,m,branch_id=None):
    qs=policies.branch_scope(m,MaintenanceCase.objects.filter(organization=m.organization),branch_id)
    return qs.filter(reported_by=request.user) if m.role=='mechanic' else qs

@router.get('/orgs/{org_id}/maintenance-cases')
def cases(request,org_id:int,vehicle_id:int|None=None,status:str|None=None,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'maintenance.read')
    qs=_case_qs(request,m,branch_id)
    if vehicle_id:qs=qs.filter(vehicle_id=vehicle_id)
    if status:qs=qs.filter(status=status)
    return paginate(qs.order_by('-id'),limit,offset,lambda c:case_dict(c))

@router.post('/orgs/{org_id}/maintenance-cases')
def create_case(request,org_id:int,payload:CaseIn):
    m=membership(request,org_id,'maintenance.request',vehicle=payload.vehicle_id)
    return case_dict(result(services.create_case,m.organization,request.user,payload.vehicle_id,payload.issue,payload.vendor,payload.quote,payload.downtime_from,payload.downtime_to))

@router.patch('/orgs/{org_id}/maintenance-cases/{case_id}')
def update_case(request,org_id:int,case_id:int,payload:CasePatch):
    m=membership(request,org_id,'maintenance.manage',case=case_id)
    return case_dict(result(services.update_case,m.organization,request.user,case_id,**payload.dict()))

@router.post('/orgs/{org_id}/maintenance-cases/{case_id}/approve')
def approve_case(request,org_id:int,case_id:int):
    m=membership(request,org_id,'maintenance.approve',case=case_id)
    return case_dict(result(services.approve_case,m.organization,request.user,case_id))

# ---- audit viewer
@router.get('/orgs/{org_id}/audit-events')
def audit_events(request,org_id:int,action:str|None=None,target_type:str|None=None,target_id:str|None=None,actor_id:int|None=None,date_from:date|None=None,date_to:date|None=None,limit:int=50,offset:int=0):
    m=membership(request,org_id,'audit.read')
    if policies.limited_branch(m):raise HttpError(403,'The audit log covers every branch; ask an admin')
    qs=AuditEvent.objects.filter(organization=m.organization)
    if action:qs=qs.filter(action__startswith=action)
    if target_type:qs=qs.filter(target_type=target_type)
    if target_id:qs=qs.filter(target_id=target_id)
    if actor_id:qs=qs.filter(actor_id=actor_id)
    if date_from:qs=qs.filter(at__gte=datetime.combine(date_from,time.min,tzinfo=timezone.utc))
    if date_to:qs=qs.filter(at__lte=datetime.combine(date_to,time.max,tzinfo=timezone.utc))
    limit=min(100,max(1,limit));offset=max(0,offset);count=qs.count()
    items=[audit_dict(a) for a in qs.order_by('-at','-id')[offset:offset+limit]]
    return {'count':count,'items':items,'next':offset+limit if offset+limit<count else None}
