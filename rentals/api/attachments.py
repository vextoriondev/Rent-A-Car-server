"""File & media API (see rentals/services/files).

Entity routes (permission = the entity's own module, through tasin_auth RULES):
    GET/POST  /orgs/{id}/{tasin-owners|tasin-drivers|tasin-clients|tasin-vehicles|tasin-agreements|tasin-maintenance|tasin-transactions}/{pk}/files
Generic routes (the handler checks the module of the file's record itself):
    GET/PATCH/DELETE /orgs/{id}/files/{file_id}      GET /orgs/{id}/files/{file_id}/content?variant=&download=
    POST /orgs/{id}/files/{file_id}/retry            POST /orgs/{id}/files/{file_id}/make-primary
    GET  /orgs/{id}/files-usage                      GET /orgs/{id}/files-limits
"""
from typing import List, Optional
from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.conf import settings
from ninja import Router, Schema, File, Form, Header, UploadedFile
from ..models import Attachment, Organization
from ..services.audit import audit
from ..services.finance import FinanceError
from ..services.files import attachments as svc
from ..services.files.storage import get_storage, StorageError
from .tasin_auth import authorize_org, tasin_auth

router = Router(auth=tasin_auth)

ROUTE_ENTITY = {'tasin-owners': 'owner', 'tasin-drivers': 'driver', 'tasin-clients': 'client', 'tasin-vehicles': 'vehicle',
                'tasin-agreements': 'agreement', 'tasin-maintenance': 'maintenance', 'tasin-transactions': 'transaction'}


def _org(org_id):
    org = Organization.objects.filter(pk=org_id).first()
    if not org:
        raise FinanceError('not_found', 'Organization not found', status=404)
    return org


def _can_edit(user, org, module):
    try:
        authorize_org(user, org, module, 'edit')
        return True
    except FinanceError:
        return False


def _view_allowed(user, org, att):
    """View permission on the record's module; identity papers (NID, passport, licence) additionally need edit permission."""
    module = svc.module_for(att)
    authorize_org(user, org, module, 'view')
    if att.category in svc.SENSITIVE_CATEGORIES and not _can_edit(user, org, module):
        raise FinanceError('forbidden', 'Identity documents are restricted to users who can edit this record', status=403)


def _attachment(org, file_id):
    att = Attachment.objects.filter(pk=file_id, organization=org).select_related('uploaded_by', 'party').first()
    if not att:
        raise FinanceError('not_found', 'File not found', status=404)
    return att


class FileMetaIn(Schema):
    description: Optional[str] = None
    category: Optional[str] = None


def _make_entity_routes(prefix, entity_type):
    @router.get(f'/orgs/{{org_id}}/{prefix}/{{pk}}/files', operation_id=f'list_files_{entity_type}')
    def list_files(request, org_id: int, pk: int):
        org = _org(org_id)
        return svc.list_for(org, entity_type, pk, include_sensitive=_can_edit(request.user, org, svc.ENTITIES[entity_type][1]))

    @router.post(f'/orgs/{{org_id}}/{prefix}/{{pk}}/files', operation_id=f'upload_files_{entity_type}')
    def upload_files(request, org_id: int, pk: int, files: List[UploadedFile] = File(...), kind: str = Form('document'),
                     category: str = Form(''), description: str = Form(''), idem: Optional[str] = Header(None, alias='Idempotency-Key')):
        org = _org(org_id)
        lim = svc.limits()
        if len(files) > lim['files']:
            raise FinanceError('too_many_files', f'At most {lim["files"]} files per upload', status=413)
        if sum(f.size for f in files) > lim['total']:
            raise FinanceError('upload_too_large', f'The upload is larger than {lim["total"] // (1024 * 1024)} MB in total', status=413)
        if kind == 'profile' and len(files) != 1:
            raise FinanceError('one_profile_picture', 'Send exactly one file for a profile picture')
        items, errors = [], []
        for i, f in enumerate(files):
            try:
                att, created = svc.upload(org, request.user, entity_type, pk, filename=f.name, data=f.read(), kind=kind, category=category,
                                          description=description, upload_key=f'{idem}:{i}' if idem else '')
                d = svc.to_dict(att, org.pk)
                d['duplicate'] = not created
                items.append(d)
            except FinanceError as e:
                if e.status in (404, 503) and not items:
                    raise
                errors.append({'fileName': f.name, 'code': e.code, 'message': str(e.message if hasattr(e, 'message') else e), 'status': e.status})
        if not items:
            first = errors[0]
            raise FinanceError(first['code'], first['message'], status=first['status'])
        return {'items': items, 'errors': errors}

    return list_files, upload_files


for _prefix, _etype in ROUTE_ENTITY.items():
    _make_entity_routes(_prefix, _etype)


def _modules(user, org, action):
    return {m for m in {v[1] for v in svc.ENTITIES.values()} if _can(user, org, m, action)}


def _can(user, org, module, action):
    try:
        authorize_org(user, org, module, action)
        return True
    except FinanceError:
        return False


@router.get('/orgs/{org_id}/files')
def central_files(request, org_id: int, entity: str = '', entity_id: int = 0, category: str = '', kind: str = 'document', q: str = '',
                  limit: int = 100, offset: int = 0):
    """The central Documents screen: every file across drivers, owners, clients, vehicles, agreements, maintenance and accounts,
    filtered by record type / record / category / text. Only modules the caller may view are included."""
    org = _org(org_id)
    return svc.central_list(org, _modules(request.user, org, 'view'), _modules(request.user, org, 'edit'), entity=entity or None, entity_id=entity_id or None,
                            category=category, kind=kind, q=q, limit=max(1, min(limit, 200)), offset=max(0, offset))


@router.post('/orgs/{org_id}/files/{file_id}/replace')
def file_replace(request, org_id: int, file_id: str, file: UploadedFile = File(...)):
    org = _org(org_id)
    att = _attachment(org, file_id)
    authorize_org(request.user, org, svc.module_for(att), 'edit')
    new = svc.replace(org, request.user, att, filename=file.name, data=file.read())
    return svc.to_dict(new, org.pk)


@router.get('/orgs/{org_id}/files-limits')
def file_limits(request, org_id: int):
    org = _org(org_id)
    authorize_org(request.user, org, '*')
    lim = svc.limits()
    return {'maxImageBytes': lim['image'], 'maxPdfBytes': lim['pdf'], 'maxDocBytes': lim['doc'], 'maxFiles': lim['files'], 'maxTotalBytes': lim['total'],
            'categories': {k: sorted(v) for k, v in svc.CATEGORIES.items()}}


@router.get('/orgs/{org_id}/files-usage')
def files_usage(request, org_id: int):
    org = _org(org_id)
    return svc.usage(org)


@router.get('/orgs/{org_id}/files/{file_id}')
def file_meta(request, org_id: int, file_id: str):
    org = _org(org_id)
    att = _attachment(org, file_id)
    _view_allowed(request.user, org, att)
    return svc.to_dict(att, org.pk)


@router.patch('/orgs/{org_id}/files/{file_id}')
def file_update(request, org_id: int, file_id: str, payload: FileMetaIn):
    org = _org(org_id)
    att = _attachment(org, file_id)
    authorize_org(request.user, org, svc.module_for(att), 'edit')
    return svc.to_dict(svc.update_meta(org, request.user, att, payload.description, payload.category), org.pk)


@router.delete('/orgs/{org_id}/files/{file_id}')
def file_delete(request, org_id: int, file_id: str):
    org = _org(org_id)
    att = _attachment(org, file_id)
    authorize_org(request.user, org, svc.module_for(att), 'delete')
    svc.delete(org, request.user, att)
    return {'success': True}


@router.post('/orgs/{org_id}/files/{file_id}/retry')
def file_retry(request, org_id: int, file_id: str):
    org = _org(org_id)
    att = _attachment(org, file_id)
    authorize_org(request.user, org, svc.module_for(att), 'edit')
    return svc.to_dict(svc.retry_processing(org, request.user, att), org.pk)


@router.post('/orgs/{org_id}/files/{file_id}/make-primary')
def file_primary(request, org_id: int, file_id: str):
    org = _org(org_id)
    att = _attachment(org, file_id)
    authorize_org(request.user, org, svc.module_for(att), 'edit')
    return svc.to_dict(svc.make_profile(org, request.user, att), org.pk)


@router.get('/orgs/{org_id}/files/{file_id}/content')
def file_content(request, org_id: int, file_id: str, variant: str = '', download: int = 0):
    """Permission is checked on every request. Object storage: a 5 minute signed link (the bytes never pass through this server);
    filesystem: streamed here. Private caching only, never shared caches."""
    org = _org(org_id)
    att = _attachment(org, file_id)
    _view_allowed(request.user, org, att)
    key, mime, name = svc.content_for(att, variant, bool(download))
    st = get_storage()
    if download:
        audit(org, request.user, 'attachment.download', svc._owner_type(att), att.owner_ref[1], after={'attachment_id': str(att.id)})
    url = st.signed_url(key, name, mime, settings.FILE_SIGNED_URL_TTL, bool(download))
    if url:
        resp = HttpResponseRedirect(url)
        resp['Cache-Control'] = 'private, no-store'
        return resp
    try:
        data = st.get(key)
    except StorageError:
        raise FinanceError('file_missing', 'The file is missing from storage', status=404)
    resp = HttpResponse(data, content_type=mime)
    safe = ''.join(c if c.isalnum() or c in '._- ' else '_' for c in name)[:120]
    resp['Content-Disposition'] = ('attachment' if download else 'inline') + f'; filename="{safe}"'
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Content-Security-Policy'] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    resp['Cache-Control'] = 'private, max-age=300'
    return resp
