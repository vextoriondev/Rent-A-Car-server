"""The one upload / download / delete service every module uses (drivers, owners, vehicles, agreements, maintenance, transactions)."""
import hashlib
import io
import logging
import os
import re
import uuid
import zipfile
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum, Count
from ...models import Attachment, Party, Vehicle, TasinAgreement, MaintenanceRecord, AccountTransaction
from ..audit import audit
from ..finance import FinanceError
from . import images
from .storage import get_storage, StorageError

log = logging.getLogger(__name__)

# entity type -> (FK field on Attachment, RBAC module, queryset factory)
ENTITIES = {
    'owner': ('party', 'owners', lambda org: Party.objects.filter(organization=org, kind=Party.Kind.OWNER)),
    'driver': ('party', 'drivers', lambda org: Party.objects.filter(organization=org, kind=Party.Kind.DRIVER)),
    'client': ('party', 'clients', lambda org: Party.objects.filter(organization=org, kind=Party.Kind.CUSTOMER)),
    'vehicle': ('vehicle', 'vehicles', lambda org: Vehicle.objects.filter(organization=org)),
    'agreement': ('agreement', 'agreements', lambda org: TasinAgreement.objects.filter(organization=org)),
    'maintenance': ('maintenance', 'maintenance', lambda org: MaintenanceRecord.objects.filter(organization=org)),
    'transaction': ('transaction', 'accounts', lambda org: AccountTransaction.objects.filter(organization=org)),
}
CATEGORIES = {
    'owner': {'nid', 'ownership', 'financial', 'other'},
    'driver': {'nid', 'license', 'passport', 'other'},
    'client': {'trade_license', 'nid', 'other'},
    'vehicle': {'registration', 'insurance', 'fitness', 'tax_token', 'inspection', 'route_permit', 'front', 'rear', 'side', 'interior', 'damage', 'other'},
    'agreement': {'agreement_pdf', 'scan', 'stamp_paper', 'signed', 'legal', 'other'},
    'maintenance': {'bill', 'invoice', 'damage', 'other'},
    'transaction': {'receipt', 'bill', 'voucher', 'other'},
}
PROFILE_ENTITIES = {'owner', 'driver', 'client', 'vehicle'}
# identity papers: visible only to users who may EDIT the module (view-only roles never see or fetch them)
SENSITIVE_CATEGORIES = {'nid', 'passport', 'license'}
KINDS = {k for k, _ in Attachment.Kind.choices}
DOC_MIME = {'application/pdf': 'pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
            'application/msword': 'doc'}
VARIANTS = ('thumb', 'medium', 'large')


def err(code, message, status=400):
    return FinanceError(code, message, status=status)


def limits():
    mb = 1024 * 1024
    return {'image': settings.ATTACH_MAX_IMAGE_MB * mb, 'pdf': settings.ATTACH_MAX_PDF_MB * mb, 'doc': settings.ATTACH_MAX_DOC_MB * mb,
            'files': settings.ATTACH_MAX_FILES, 'total': settings.ATTACH_MAX_TOTAL_MB * mb}


def scan_hook(data: bytes, mime: str) -> None:
    """Extension point for a malware scanner (e.g. ClamAV). Raise FinanceError to reject. None is configured by default."""
    return None


def sniff(data: bytes):
    """Decide the file type from its CONTENT. -> (mime, ext, width, height) or raises FinanceError."""
    if not data:
        raise err('empty_file', 'The file is empty')
    if data.startswith(b'%PDF-'):
        return 'application/pdf', 'pdf', None, None
    if data.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
                    raise err('invalid_file', 'The document is not acceptable')
        except zipfile.BadZipFile:
            raise err('invalid_file', 'The file content does not match a supported type')
        if '[Content_Types].xml' in names and any(n.startswith('word/') for n in names):
            if any(n.lower().endswith('vbaproject.bin') for n in names):
                raise err('macros_not_allowed', 'Word files with macros are not accepted')
            return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx', None, None
        raise err('unsupported_type', 'Only images, PDF and Word files are accepted')
    if data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1') and 'WordDocument'.encode('utf-16-le') in data:
        return 'application/msword', 'doc', None, None
    try:
        mime, ext, w, h = images.inspect(data)
    except images.ImageRejected as e:
        raise err(str(e), 'The file is not a supported image, PDF or Word document')
    return mime, ext, w, h


def check_size(mime, size):
    lim = limits()
    kind = 'image' if mime.startswith('image/') else 'pdf' if mime == 'application/pdf' else 'doc'
    if size > lim[kind]:
        raise err('file_too_large', f'This {kind} is larger than the {lim[kind] // (1024 * 1024)} MB limit', status=413)


def get_entity(org, entity_type, pk):
    spec = ENTITIES.get(entity_type)
    if not spec:
        raise err('unknown_entity', 'Unsupported record type')
    obj = spec[2](org).filter(pk=pk).first()
    if not obj:
        raise err('not_found', 'Record not found', status=404)
    return spec[0], obj


def _clean_name(name):
    base = os.path.basename((name or '').replace('\\', '/')).strip()
    base = re.sub(r'[^\w.\- ()]', '_', base, flags=re.UNICODE)[:150]
    return base or 'file'


def _doc_like(kind, category):
    return kind == 'document'      # scans, receipts, certificates: keep the original and use the higher quality


def _keys_for(org, entity_type, att_id):
    return f'org-{org.pk}/{entity_type}/{att_id}'


def build_objects(org, entity_type, att_id, data, mime, ext, kind, category):
    """Process the bytes into storage objects. -> dict of fields for the Attachment, plus [(key, bytes, content_type)]."""
    base = _keys_for(org, entity_type, att_id)
    puts, fields = [], {}
    if mime.startswith('image/'):
        doc_like = _doc_like(kind, category)
        variants, (w0, h0) = images.build_variants(data, doc_like)
        meta = {}
        for name, (blob, w, h) in variants.items():
            key = f'{base}/{name}.webp'
            puts.append((key, blob, 'image/webp'))
            meta[name] = {'key': key, 'size': len(blob), 'width': w, 'height': h}
        if doc_like:
            keep = images.strip_metadata(data, mime) if images.has_gps(data) else data
            okey = f'{base}/original.{ext}'
            puts.append((okey, keep, mime))
            fields.update(storage_key=okey, original_key=okey, mime_type=mime, extension=ext, size_stored=len(keep))
        else:
            fields.update(storage_key=meta['large']['key'], original_key='', mime_type='image/webp', extension='webp', size_stored=meta['large']['size'])
        fields.update(variants=meta, width=w0, height=h0)
    else:
        key = f'{base}/file.{ext}'
        puts.append((key, data, mime))
        fields.update(storage_key=key, original_key='', mime_type=mime, extension=ext, size_stored=len(data), variants={})
    return fields, puts


def _write(puts):
    st = get_storage()
    done = []
    try:
        for key, blob, ctype in puts:
            st.put(key, blob, ctype)
            done.append(key)
    except StorageError:
        _delete_keys(done)
        raise err('storage_unavailable', 'File storage is not available right now, please try again', status=503)
    return done


def _delete_keys(keys):
    st = get_storage()
    for k in keys:
        try:
            st.delete(k)
        except Exception:                      # never let a storage hiccup undo a database decision; the cleanup command sweeps strays
            log.warning('could not delete storage object %s', k)


def validate_request(entity_type, kind, category):
    if entity_type not in ENTITIES:
        raise err('unknown_entity', 'Unsupported record type')
    if kind not in KINDS:
        raise err('invalid_kind', 'Unknown attachment kind')
    if kind == 'profile' and entity_type not in PROFILE_ENTITIES:
        raise err('no_profile_picture', 'This record has no profile picture')
    category = (category or '').strip().lower()
    if category and category not in CATEGORIES[entity_type]:
        raise err('invalid_category', 'Unknown attachment category')
    return category


def to_dict(a, org_id=None):
    org_id = org_id or a.organization_id
    base = f'/orgs/{org_id}/files/{a.id}'
    return {
        'id': str(a.id), 'kind': a.kind, 'category': a.category, 'description': a.description, 'fileName': a.original_name,
        'mimeType': a.mime_type, 'extension': a.extension, 'isImage': a.mime_type.startswith('image/'),
        'sizeOriginal': a.size_original, 'sizeStored': a.size_stored, 'width': a.width, 'height': a.height,
        'status': a.status, 'error': a.error, 'hasThumb': 'thumb' in (a.variants or {}),
        'uploadedAt': a.created_at.isoformat(), 'uploadedBy': a.uploaded_by.get_username() if a.uploaded_by_id else '',
        'urls': {'view': f'{base}/content?variant=large', 'thumb': f'{base}/content?variant=thumb', 'medium': f'{base}/content?variant=medium',
                 'download': f'{base}/content?download=1'},
    }


def list_for(org, entity_type, pk, include_sensitive=True):
    field, obj = get_entity(org, entity_type, pk)
    rows = Attachment.objects.filter(organization=org, **{field: obj.pk}).select_related('uploaded_by').order_by('kind', '-created_at')
    if not include_sensitive:
        rows = rows.exclude(category__in=SENSITIVE_CATEGORIES)
    return [to_dict(a, org.pk) for a in rows]


def upload(org, user, entity_type, pk, *, filename, data, kind='document', category='', description='', upload_key=''):
    category = validate_request(entity_type, kind, category)
    field, entity = get_entity(org, entity_type, pk)
    description = (description or '').strip()[:500]
    upload_key = (upload_key or '').strip()[:80]
    if upload_key:
        prior = Attachment.objects.filter(organization=org, upload_key=upload_key).first()
        if prior:
            return prior, False            # retry of an upload that already succeeded
    mime, ext, w, h = sniff(data)
    check_size(mime, len(data))
    if kind in ('profile', 'photo') and not mime.startswith('image/'):
        raise err('image_required', 'Photos must be image files')
    scan_hook(data, mime)
    sha = hashlib.sha256(data).hexdigest()
    att_id = uuid.uuid4()
    status, error = Attachment.Status.COMPLETED, ''
    try:
        fields, puts = build_objects(org, entity_type, att_id, data, mime, ext, kind, category)
    except images.ImageRejected as e:
        # the bytes look like an image but cannot be processed: keep the original so nothing is lost, mark it failed, allow a retry
        key = f'{_keys_for(org, entity_type, att_id)}/original.{ext}'
        fields, puts = {'storage_key': key, 'original_key': key, 'mime_type': mime, 'extension': ext, 'size_stored': len(data), 'variants': {}, 'width': w, 'height': h}, [(key, data, mime)]
        status, error = Attachment.Status.FAILED, str(e)[:250]
    written = _write(puts)
    old_keys = []
    try:
        with transaction.atomic():
            if type(entity).objects.select_for_update().filter(pk=entity.pk).first() is None:      # serialize uploads per record; refuse a record deleted meanwhile
                raise err('not_found', 'Record not found', status=404)
            existing = Attachment.objects.filter(organization=org, **{field: entity.pk})
            dup = existing.filter(sha256=sha, kind=kind).first()
            if dup:
                _delete_keys(written)
                return dup, False              # the same file is already attached: never store it twice
            if kind == 'profile':
                for old in existing.filter(kind='profile'):
                    old_keys += old.all_keys()
                    old.delete()
            att = Attachment.objects.create(
                id=att_id, organization=org, kind=kind, category=category, description=description, original_name=_clean_name(filename),
                stored_name=f'{att_id.hex}.{fields["extension"]}', storage_provider=get_storage().provider, size_original=len(data), sha256=sha,
                status=status, error=error, upload_key=upload_key, uploaded_by=user if getattr(user, 'pk', None) else None,
                **{field: entity}, **fields)
            audit(org, user, 'attachment.upload', entity_type, entity.pk, after={'attachment_id': str(att.id), 'name': att.original_name, 'kind': kind, 'category': category,
                                                                              'size_original': att.size_original, 'size_stored': att.size_stored, 'sha256': sha})
            transaction.on_commit(lambda: _delete_keys(old_keys))
    except FinanceError:
        _delete_keys(written)
        raise
    except IntegrityError:
        _delete_keys(written)
        winner = Attachment.objects.filter(organization=org, upload_key=upload_key).first() if upload_key else None
        if winner:
            return winner, False
        raise err('conflict', 'Another upload for this record was in progress, please retry', status=409)
    return att, True


def retry_processing(org, user, att):
    if att.status != Attachment.Status.FAILED:
        raise err('not_failed', 'Only failed uploads can be retried')
    data = get_storage().get(att.original_key or att.storage_key)
    kind, category = att.kind, att.category
    fields, puts = build_objects(att.organization, _owner_type(att), att.id, data, att.mime_type, att.extension, kind, category)
    _write(puts)
    old = set(att.all_keys())
    for k, v in fields.items():
        setattr(att, k, v)
    att.status, att.error = Attachment.Status.COMPLETED, ''
    att.save()
    _delete_keys(list(old - set(att.all_keys())))
    audit(att.organization, user, 'attachment.reprocess', _owner_type(att), att.owner_ref[1], after={'attachment_id': str(att.id)})
    return att


def _owner_type(att):
    field, pk = att.owner_ref
    if field == 'party':
        return {'owner': 'owner', 'driver': 'driver', 'customer': 'client'}.get(att.party.kind, 'owner')
    return field


def module_for(att):
    return ENTITIES[_owner_type(att)][1]


def update_meta(org, user, att, description=None, category=None):
    before = {'description': att.description, 'category': att.category}
    if category is not None:
        category = category.strip().lower()
        if category and category not in CATEGORIES[_owner_type(att)]:
            raise err('invalid_category', 'Unknown attachment category')
        att.category = category
    if description is not None:
        att.description = description.strip()[:500]
    att.save()
    audit(org, user, 'attachment.update', _owner_type(att), att.owner_ref[1], before=before, after={'description': att.description, 'category': att.category})
    return att


def make_profile(org, user, att):
    """Promote a photo to the record's main picture; the previous main picture becomes an ordinary photo."""
    etype = _owner_type(att)
    if etype not in PROFILE_ENTITIES or not att.mime_type.startswith('image/') and not att.variants:
        raise err('no_profile_picture', 'Only photos can be the main picture')
    if att.kind == 'document':
        raise err('no_profile_picture', 'A document cannot be the main picture')
    field, pk = att.owner_ref
    with transaction.atomic():
        type(getattr(att, field)).objects.select_for_update().filter(pk=pk).first()
        for cur in Attachment.objects.filter(organization=org, kind='profile', **{field: pk}).exclude(pk=att.pk):
            cur.kind = 'photo'
            cur.save(update_fields=['kind', 'updated_at'])
        att.kind = 'profile'
        att.save(update_fields=['kind', 'updated_at'])
        audit(org, user, 'attachment.make_primary', etype, pk, after={'attachment_id': str(att.id)})
    return att


def delete(org, user, att):
    keys = att.all_keys()
    info = {'attachment_id': str(att.id), 'name': att.original_name, 'kind': att.kind, 'category': att.category, 'sha256': att.sha256}
    etype, (_, pk) = _owner_type(att), att.owner_ref
    with transaction.atomic():
        if not Attachment.objects.select_for_update().filter(pk=att.pk).exists():      # a concurrent delete already removed it
            raise err('not_found', 'File not found', status=404)
        att.delete()
        audit(org, user, 'attachment.delete', etype, pk, before=info)
        transaction.on_commit(lambda: _delete_keys(keys))      # storage is touched only after the database commit succeeded


def content_for(att, variant=None, download=False):
    """-> (key, mime, filename). `variant`: thumb | medium | large | original. Images served inline as WebP variants by default."""
    name = att.original_name
    if download or variant in (None, '', 'original'):
        key, mime = (att.original_key or att.storage_key), att.mime_type
    elif variant in VARIANTS and variant in (att.variants or {}):
        key, mime = att.variants[variant]['key'], 'image/webp'
        name = os.path.splitext(name)[0] + '.webp'
    else:
        key, mime = att.storage_key, att.mime_type
    return key, mime, name


def usage(org):
    rows = Attachment.objects.filter(organization=org)
    total = rows.aggregate(n=Count('id'), size=Sum('size_stored'), orig=Sum('size_original'))
    variant_bytes = 0
    for a in rows.exclude(variants={}).only('variants', 'storage_key', 'original_key'):
        seen = set()
        for v in a.variants.values():
            if v['key'] not in seen and v['key'] != a.storage_key:
                variant_bytes += v['size']
            seen.add(v['key'])
    by_module = {}
    for etype, (field, _m, _q) in ENTITIES.items():
        q = rows.filter(**{field + '__isnull': False})
        if field == 'party':
            q = q.filter(party__kind={'owner': 'owner', 'driver': 'driver', 'client': 'customer'}[etype])
        agg = q.aggregate(n=Count('id'), size=Sum('size_stored'))
        by_module[etype] = {'files': agg['n'] or 0, 'bytes': agg['size'] or 0}
    images_n = rows.filter(mime_type__startswith='image/').count()
    largest = [{'id': str(a.id), 'fileName': a.original_name, 'bytes': a.size_stored, 'kind': a.kind}
               for a in rows.order_by('-size_stored')[:10]]
    return {'files': total['n'] or 0, 'images': images_n, 'documents': (total['n'] or 0) - images_n,
            'bytesMain': total['size'] or 0, 'bytesVariants': variant_bytes, 'bytesTotal': (total['size'] or 0) + variant_bytes,
            'bytesOriginalUploaded': total['orig'] or 0, 'byModule': by_module, 'largest': largest,
            'failed': rows.filter(status='failed').count(), 'backend': get_storage().provider}


NO_PHOTO = {'photoId': '', 'photoUrl': '', 'thumbUrl': ''}


def profile_map(org, field):
    """{record pk: photo urls} for every profile picture of `field` ('party' | 'vehicle') in one query, for list screens."""
    out = {}
    for pk, att_id, has_thumb in Attachment.objects.filter(organization=org, kind='profile', **{field + '__isnull': False}).values_list(field + '_id', 'id', 'status'):
        if has_thumb != 'completed':
            continue
        base = f'/orgs/{org.pk}/files/{att_id}/content'
        out[pk] = {'photoId': str(att_id), 'photoUrl': base + '?variant=large', 'thumbUrl': base + '?variant=thumb'}
    return out
