"""Private documents behind signed URLs. Files never get a public path: the API hands out a short-lived, user-bound token,
and every issued URL is written to the audit log."""
import hashlib
import os
import re
import uuid
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core import signing
from .. import policies
from ..models import Agreement, Charge, DriverAgreement, Document, Expense, MaintenanceCase, Party, Payment, Vehicle
from .audit import audit
from .helpers import DomainError

MAX_BYTES=10*1024*1024
ALLOWED={'application/pdf':'.pdf','image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}
SALT='rentflow.document'
TARGETS={'driver_agreement':DriverAgreement,'charge':Charge,'expense':Expense,'payment':Payment,'agreement':Agreement,'vehicle':Vehicle,'party':Party,'maintenance_case':MaintenanceCase}
MECHANIC_TARGETS={'maintenance_case','vehicle'}

def storage():
    return FileSystemStorage(location=str(settings.PRIVATE_MEDIA_ROOT))

def scan_hook(data:bytes,content_type:str) -> None:
    """Extension point for a virus scanner (e.g. ClamAV). Raise DomainError to reject the upload. Default: no scanner configured."""
    return None

def _sniff(data:bytes) -> str|None:
    if data.startswith(b'%PDF-'):return 'application/pdf'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP':return 'image/webp'
    return None

def _target(org,target_type,target_id):
    model=TARGETS.get(target_type)
    if not model:raise DomainError('unsupported_document_target')
    filt={'period__organization':org} if model is Expense else {'organization':org}
    return model.objects.get(pk=target_id,**filt)

def _clean_name(name:str) -> str:
    base=os.path.basename(name or '').strip()
    base=re.sub(r'[^A-Za-z0-9._ -]','_',base)[:120]
    return base or 'document'

def upload_document(org,user,target_type,target_id,filename,content_type,data:bytes,role='admin',category=''):
    if category not in('','identity'):raise DomainError('unknown_document_category')
    if category=='identity' and (role!='admin' or target_type!='party'):raise DomainError('identity_documents_are_filed_by_an_administrator')
    if role=='mechanic' and target_type not in MECHANIC_TARGETS:raise DomainError('mechanics_can_only_attach_files_to_maintenance')
    _target(org,target_type,target_id)
    if not data:raise DomainError('the_file_is_empty')
    if len(data)>MAX_BYTES:raise DomainError('files_may_be_at_most_10_mb')
    if content_type not in ALLOWED:raise DomainError('only_pdf_png_jpeg_or_webp_files')
    if _sniff(data)!=content_type:raise DomainError('the_file_content_does_not_match_its')
    scan_hook(data,content_type)
    key=f'{org.pk}/{uuid.uuid4().hex}{ALLOWED[content_type]}'
    storage().save(key,ContentFile(data))
    doc=Document.objects.create(organization=org,target_type=target_type,target_id=str(target_id),storage_key=key,original_name=_clean_name(filename),content_type=content_type,size=len(data),sha256=hashlib.sha256(data).hexdigest(),category=category,uploaded_by=user)
    audit(org,user,'document.upload',target_type,target_id,after={'document_id':doc.pk,'name':doc.original_name,'size':doc.size,'sha256':doc.sha256})
    return doc

def _removable(doc,target):
    if doc.target_type=='expense':return target.state==Expense.State.DRAFT
    if doc.target_type=='agreement':return target.status=='draft'
    if doc.target_type=='driver_agreement':return True  # a signed copy may be replaced; the file hash stays in the audit trail
    return False

def delete_document(org,user,document_id):
    """Only while the parent is still a draft; after posting the evidence stays."""
    doc=Document.objects.get(pk=document_id,organization=org)
    if not _removable(doc,_target(org,doc.target_type,doc.target_id)):raise DomainError('documents_on_posted_records_are_kept_only')
    info={'document_id':doc.pk,'name':doc.original_name,'sha256':doc.sha256}
    target_type,target_id=doc.target_type,doc.target_id
    storage().delete(doc.storage_key);doc.delete()
    audit(org,user,'document.delete',target_type,target_id,before=info)

def in_branch(member,target_type,target_id) -> bool:
    """False when a branch-limited member points at a record of another branch (or a target type that does not exist)."""
    model=TARGETS.get(target_type)
    if not model:return False
    if not policies.limited_branch(member):return True
    return policies.branch_scope(member,model.objects.filter(pk=target_id)).exists()

def can_access(member,doc) -> bool:
    if not in_branch(member,doc.target_type,doc.target_id):return False
    if doc.category=='identity':return member.role=='admin'
    if member.role in('admin','accountant','operations','viewer'):return True
    if member.role=='mechanic':return doc.target_type in MECHANIC_TARGETS
    return False

def list_documents(org,member,target_type,target_id):
    qs=Document.objects.filter(organization=org,target_type=target_type,target_id=str(target_id)).order_by('-id')
    return [d for d in qs if can_access(member,d)]

def issue_url(org,member,user,document_id):
    doc=Document.objects.get(pk=document_id,organization=org)
    if not can_access(member,doc):raise DomainError('you_cannot_open_this_document')
    token=signing.TimestampSigner(salt=SALT).sign(f'{doc.pk}:{user.pk}')
    audit(org,user,'document.access',doc.target_type,doc.target_id,after={'document_id':doc.pk})
    return {'url':f'/documents/{doc.pk}/download?token={token}','expires_in':settings.DOCUMENT_URL_MAX_AGE}

def resolve_token(token:str,user):
    """Verify signature, age and that the token was issued to this very user; returns the Document."""
    try:value=signing.TimestampSigner(salt=SALT).unsign(token,max_age=settings.DOCUMENT_URL_MAX_AGE)
    except signing.SignatureExpired as exc:raise DomainError('this_link_has_expired') from exc
    except signing.BadSignature as exc:raise DomainError('invalid_link') from exc
    doc_id,user_id=value.split(':')
    if str(user.pk)!=user_id:raise DomainError('this_link_was_issued_to_another_user')
    return Document.objects.get(pk=doc_id)

def read_bytes(doc):
    with storage().open(doc.storage_key,'rb') as fh:data=fh.read()
    if hashlib.sha256(data).hexdigest()!=doc.sha256:raise DomainError('stored_file_failed_its_integrity_check')
    return data
