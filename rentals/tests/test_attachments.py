"""File & media system: storage, optimization, validation, permissions, idempotency, relations (SQLite; PostgreSQL in test_pg_concurrency_attachments)."""
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import date
from decimal import Decimal as D
from unittest import mock
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, Client, override_settings
from PIL import Image
from rentals.models import (Organization, Membership, Party, Vehicle, TasinAgreement, MaintenanceRecord, AccountTransaction, Attachment, AuditEvent)
from rentals.services.files import attachments as svc, images
from rentals.services.files.storage import get_storage, reset_storage

User = get_user_model()


def jpeg(w=1200, h=800, exif_orientation=None, gps=False, noise=True, color=(200, 80, 40)):
    im = Image.effect_noise((w, h), 60).convert('RGB') if noise else Image.new('RGB', (w, h), color)
    exif = Image.Exif()
    if exif_orientation:
        exif[0x0112] = exif_orientation
    if gps:
        exif[0x8825] = {1: 'N', 2: (23.0, 45.0, 10.0), 3: 'E', 4: (90.0, 24.0, 5.0)}
    out = io.BytesIO()
    im.save(out, 'JPEG', quality=95, exif=exif if (exif_orientation or gps) else b'')
    return out.getvalue()


def png_alpha(w=200, h=120):
    im = Image.new('RGBA', (w, h), (255, 0, 0, 0))
    for x in range(w // 2):
        for y in range(h):
            im.putpixel((x, y), (0, 128, 255, 255))
    out = io.BytesIO()
    im.save(out, 'PNG')
    return out.getvalue()


def docx_bytes(macro=False):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<w:document/>')
        if macro:
            z.writestr('word/vbaProject.bin', b'x')
    return out.getvalue()


PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF'


class Base(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ov = override_settings(PRIVATE_MEDIA_ROOT=self.tmp, FILE_STORAGE_BACKEND='filesystem')
        self.ov.enable()
        reset_storage()
        self.org = Organization.objects.create(name='Att')
        self.org2 = Organization.objects.create(name='Other')
        self.admin = User.objects.create_user('att_admin', password='x')
        Membership.objects.create(organization=self.org, user=self.admin, role='admin', is_super_admin=True)
        self.c = Client()
        self.c.force_login(self.admin)
        self.driver = Party.objects.create(organization=self.org, kind='driver', name='Drv')
        self.owner = Party.objects.create(organization=self.org, kind='owner', name='Own')
        self.car = Vehicle.objects.create(organization=self.org, registration='ATT-1', model_name='m', owner=self.owner)
        self.agr = TasinAgreement.objects.create(organization=self.org, agreement_type='owner', party=self.owner, vehicle=self.car, title='A', start_date=date(2026, 1, 1))
        self.mnt = MaintenanceRecord.objects.create(organization=self.org, maintenance_id='MNT-1', vehicle=self.car, vehicle_reg='ATT-1', garage_name='G', date=date(2026, 1, 1), problem_description='x')
        self.txn = AccountTransaction.objects.create(organization=self.org, transaction_id='TXN-1', date=date(2026, 1, 1), transaction_type='Expense', category='Fuel', amount=D('10'))

    def tearDown(self):
        reset_storage()
        self.ov.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def url(self, kind='tasin-drivers', pk=None, org=None):
        pk = pk or {'tasin-drivers': self.driver.pk, 'tasin-owners': self.owner.pk, 'tasin-vehicles': self.car.pk, 'tasin-agreements': self.agr.pk,
                    'tasin-maintenance': self.mnt.pk, 'tasin-transactions': self.txn.pk}[kind]
        return f'/api/v1/orgs/{(org or self.org).pk}/{kind}/{pk}/files'

    def up(self, files, kind='document', category='', client=None, url=None, idem=None, description=''):
        c = client or self.c
        data = {'files': [SimpleUploadedFile(n, b, content_type=ct) for n, b, ct in files], 'kind': kind, 'category': category, 'description': description}
        extra = {'HTTP_IDEMPOTENCY_KEY': idem} if idem else {}
        return c.post(url or self.url(), data, **extra)


class OptimizationTests(Base):
    def test_phone_photo_is_optimized_and_variants_are_webp(self):
        raw = jpeg(3600, 2400)
        r = self.up([('p.jpg', raw, 'image/jpeg')], kind='profile')
        self.assertEqual(r.status_code, 200, r.content)
        a = Attachment.objects.get()
        self.assertEqual(a.mime_type, 'image/webp')
        self.assertEqual(set(a.variants), {'thumb', 'medium', 'large'})
        self.assertLessEqual(max(a.variants['large']['width'], a.variants['large']['height']), 1600)
        self.assertLessEqual(max(a.variants['thumb']['width'], a.variants['thumb']['height']), 240)
        self.assertLess(a.size_stored, len(raw) * 0.6)                       # far smaller than the camera original
        self.assertEqual(a.size_original, len(raw))
        for v in a.variants.values():
            im = Image.open(io.BytesIO(get_storage().get(v['key'])))
            self.assertEqual(im.format, 'WEBP')
            self.assertEqual(im.size, (v['width'], v['height']))
        self.assertEqual(a.original_key, '')                                 # photos: no duplicate original

    def test_no_upscaling_of_small_images(self):
        self.up([('s.jpg', jpeg(300, 200), 'image/jpeg')], kind='photo')
        a = Attachment.objects.get()
        self.assertEqual((a.variants['large']['width'], a.variants['large']['height']), (300, 200))
        self.assertEqual((a.variants['medium']['width'], a.variants['medium']['height']), (300, 200))

    def test_exif_orientation_applied_and_gps_removed(self):
        raw = jpeg(400, 200, exif_orientation=6, gps=True)
        self.assertTrue(images.has_gps(raw))
        self.up([('o.jpg', raw, 'image/jpeg')], kind='photo')
        a = Attachment.objects.get()
        self.assertEqual((a.variants['large']['width'], a.variants['large']['height']), (200, 400))   # rotated upright
        for v in a.variants.values():
            im = Image.open(io.BytesIO(get_storage().get(v['key'])))
            self.assertFalse(im.getexif().get_ifd(0x8825))
            self.assertNotIn(b'GPS', get_storage().get(v['key'])[:2000])

    def test_png_transparency_is_preserved(self):
        self.up([('t.png', png_alpha(), 'image/png')], kind='photo')
        a = Attachment.objects.get()
        im = Image.open(io.BytesIO(get_storage().get(a.variants['large']['key'])))
        self.assertEqual(im.mode, 'RGBA')
        self.assertEqual(im.getpixel((190, 5))[3], 0)

    def test_document_scan_keeps_original_and_higher_quality(self):
        raw = jpeg(2400, 1800)
        self.up([('scan.jpg', raw, 'image/jpeg')], kind='document', category='registration', url=self.url('tasin-vehicles'))
        a = Attachment.objects.get()
        self.assertEqual(a.mime_type, 'image/jpeg')
        self.assertEqual(get_storage().get(a.original_key), raw)             # byte-for-byte original
        self.assertGreaterEqual(a.variants['large']['width'], 2200 if 2400 > 2200 else 2400)
        self.assertIn('thumb', a.variants)

    def test_document_scan_with_gps_gets_clean_original(self):
        raw = jpeg(800, 600, gps=True)
        self.up([('scan.jpg', raw, 'image/jpeg')], kind='document', category='nid')
        a = Attachment.objects.get()
        self.assertFalse(images.has_gps(get_storage().get(a.original_key)))

    def test_measured_reduction_is_reported(self):
        raw = jpeg(4000, 3000)
        self.up([('big.jpg', raw, 'image/jpeg')], kind='photo')
        a = Attachment.objects.get()
        print(f'\n[measure] original {len(raw)} B -> stored {a.size_stored} B ({100 - 100 * a.size_stored / len(raw):.1f}% smaller); '
              f'thumb {a.variants["thumb"]["size"]} B, medium {a.variants["medium"]["size"]} B')
        self.assertLess(a.size_stored, len(raw))


class ValidationTests(Base):
    def test_accepts_pdf_docx_doc_and_images(self):
        doc = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + 'WordDocument'.encode('utf-16-le') + b'0' * 100
        r = self.up([('a.pdf', PDF, 'application/pdf'), ('b.docx', docx_bytes(), 'application/octet-stream'), ('c.doc', doc, 'application/msword'),
                     ('d.jpg', jpeg(100, 100), 'image/jpeg'), ('e.png', png_alpha(), 'image/png')], category='other')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.json()['items']), 5)
        self.assertEqual({a.extension for a in Attachment.objects.all()}, {'pdf', 'docx', 'doc', 'jpg', 'png'})

    def test_rejects_disguised_and_invalid_files(self):
        bad = [('x.jpg', b'MZ\x90\x00 this is an executable', 'image/jpeg'), ('y.pdf', b'<html><script>alert(1)</script></html>', 'application/pdf'),
               ('z.jpg', jpeg(100, 100)[:300], 'image/jpeg'), ('s.svg', b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'image/svg+xml'),
               ('e.jpg', b'', 'image/jpeg'), ('m.docx', docx_bytes(macro=True), 'application/octet-stream'), ('g.gif', b'GIF89a\x01\x00\x01\x00', 'image/gif')]
        for name, blob, ct in bad:
            r = self.up([(name, blob, ct)])
            self.assertEqual(r.status_code, 400, (name, r.content))
        self.assertEqual(Attachment.objects.count(), 0)

    def test_extension_is_not_trusted_content_decides(self):
        r = self.up([('photo.pdf', jpeg(100, 100), 'application/pdf')], kind='photo')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Attachment.objects.get().mime_type, 'image/webp')

    def test_photo_kind_requires_an_image(self):
        r = self.up([('a.pdf', PDF, 'application/pdf')], kind='photo')
        self.assertEqual(r.status_code, 400)

    @override_settings(ATTACH_MAX_IMAGE_MB=1)
    def test_oversized_image_rejected_413(self):
        r = self.up([('big.png', b'\x89PNG\r\n\x1a\n' + b'0' * (1024 * 1024 + 10), 'image/png')])
        self.assertIn(r.status_code, (400, 413))
        big = jpeg(2500, 2500)
        self.assertGreater(len(big), 1024 * 1024)
        r = self.up([('big.jpg', big, 'image/jpeg')])
        self.assertEqual(r.status_code, 413, r.content)

    @override_settings(ATTACH_MAX_FILES=2)
    def test_too_many_files(self):
        r = self.up([(f'{i}.pdf', PDF + bytes([i]), 'application/pdf') for i in range(3)])
        self.assertEqual(r.status_code, 413)

    @override_settings(ATTACH_MAX_TOTAL_MB=1)
    def test_total_size_limit(self):
        big = PDF + b'0' * 700_000
        r = self.up([('1.pdf', big, 'application/pdf'), ('2.pdf', big + b'1', 'application/pdf')])
        self.assertEqual(r.status_code, 413)

    def test_pixel_bomb_rejected(self):
        with override_settings(ATTACH_MAX_PIXELS=10_000):
            r = self.up([('b.jpg', jpeg(200, 200), 'image/jpeg')], kind='photo')
        self.assertEqual(r.status_code, 400)

    def test_partial_success_reports_per_file_errors(self):
        r = self.up([('ok.pdf', PDF, 'application/pdf'), ('bad.jpg', b'not an image', 'image/jpeg')])
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body['items']), 1)
        self.assertEqual(body['errors'][0]['fileName'], 'bad.jpg')

    def test_unknown_category_and_profile_on_agreement_rejected(self):
        self.assertEqual(self.up([('a.pdf', PDF, 'application/pdf')], category='nonsense').status_code, 400)
        r = self.up([('p.jpg', jpeg(100, 100), 'image/jpeg')], kind='profile', url=self.url('tasin-agreements'))
        self.assertEqual(r.status_code, 400)

    def test_filename_never_controls_the_storage_path(self):
        r = self.up([('../../etc/passwd.pdf', PDF, 'application/pdf')])
        self.assertEqual(r.status_code, 200)
        a = Attachment.objects.get()
        self.assertNotIn('..', a.storage_key)
        self.assertTrue(a.storage_key.startswith(f'org-{self.org.pk}/driver/'))
        self.assertEqual(a.original_name, 'passwd.pdf')


class LifecycleTests(Base):
    def test_profile_is_replaced_and_old_objects_removed(self):
        self.up([('a.jpg', jpeg(500, 500), 'image/jpeg')], kind='profile')
        first = Attachment.objects.get()
        old_keys = first.all_keys()
        with self.captureOnCommitCallbacks(execute=True):
            self.up([('b.jpg', jpeg(500, 500, color=(1, 2, 3)), 'image/jpeg')], kind='profile')
        self.assertEqual(Attachment.objects.filter(party=self.driver, kind='profile').count(), 1)
        self.assertNotEqual(Attachment.objects.get().id, first.id)
        for k in old_keys:
            self.assertFalse(get_storage().exists(k))
        for k in Attachment.objects.get().all_keys():
            self.assertTrue(get_storage().exists(k))

    def test_same_file_twice_is_stored_once_and_retry_key_is_idempotent(self):
        blob = jpeg(300, 300)
        self.up([('a.jpg', blob, 'image/jpeg')], kind='photo', idem='k1')
        self.up([('a.jpg', blob, 'image/jpeg')], kind='photo', idem='k1')       # network retry
        self.up([('renamed.jpg', blob, 'image/jpeg')], kind='photo')            # same bytes again
        self.assertEqual(Attachment.objects.count(), 1)

    def test_same_file_on_two_organizations_is_kept_separately(self):
        blob = PDF + b'shared'
        o2 = Party.objects.create(organization=self.org2, kind='driver', name='X')
        self.up([('a.pdf', blob, 'application/pdf')])
        a2 = User.objects.create_user('o2', password='x')
        Membership.objects.create(organization=self.org2, user=a2, role='admin', is_super_admin=True)
        c2 = Client(); c2.force_login(a2)
        self.up([('a.pdf', blob, 'application/pdf')], client=c2, url=f'/api/v1/orgs/{self.org2.pk}/tasin-drivers/{o2.pk}/files')
        keys = list(Attachment.objects.values_list('storage_key', flat=True))
        self.assertEqual(len(set(keys)), 2)

    def test_delete_removes_row_and_objects_and_leaves_others(self):
        self.up([('a.pdf', PDF, 'application/pdf'), ('b.pdf', PDF + b'2', 'application/pdf')])
        a, b = list(Attachment.objects.order_by('original_name'))
        keys = a.all_keys()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.c.delete(f'/api/v1/orgs/{self.org.pk}/files/{a.id}')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Attachment.objects.filter(pk=a.pk).exists())
        self.assertTrue(Attachment.objects.filter(pk=b.pk).exists())
        for k in keys:
            self.assertFalse(get_storage().exists(k))
        self.assertTrue(get_storage().exists(b.storage_key))
        self.assertTrue(AuditEvent.objects.filter(action='attachment.delete').exists())

    def test_storage_failure_on_delete_does_not_break_the_database_decision(self):
        self.up([('a.pdf', PDF, 'application/pdf')])
        a = Attachment.objects.get()
        with mock.patch.object(type(get_storage()), 'delete', side_effect=RuntimeError('boom')):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.c.delete(f'/api/v1/orgs/{self.org.pk}/files/{a.id}')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Attachment.objects.exists())

    def test_database_write_failure_leaves_no_storage_objects(self):
        with mock.patch('rentals.services.files.attachments.audit', side_effect=IntegrityError('x')):
            r = self.up([('a.pdf', PDF, 'application/pdf')])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(list(get_storage().iter_keys()), [])
        self.assertEqual(Attachment.objects.count(), 0)

    def test_storage_failure_on_upload_is_503_and_leaves_no_row(self):
        from rentals.services.files.storage import StorageError
        with mock.patch.object(type(get_storage()), 'put', side_effect=StorageError('down')):
            r = self.up([('a.pdf', PDF, 'application/pdf')])
        self.assertEqual(r.status_code, 503)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_deleting_the_record_deletes_its_attachment_rows(self):
        self.up([('a.pdf', PDF, 'application/pdf')], url=self.url('tasin-transactions'))
        self.assertEqual(Attachment.objects.count(), 1)
        self.txn.delete()
        self.assertEqual(Attachment.objects.count(), 0)

    def test_database_refuses_an_attachment_with_no_or_two_owners(self):
        base = dict(organization=self.org, original_name='x', stored_name='x', mime_type='application/pdf', extension='pdf', sha256='0' * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attachment.objects.create(**base)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attachment.objects.create(party=self.driver, vehicle=self.car, **base)

    def test_make_primary_swaps_main_picture(self):
        self.up([('m.jpg', jpeg(300, 300), 'image/jpeg')], kind='profile', url=self.url('tasin-vehicles'))
        self.up([('g.jpg', jpeg(300, 300, color=(9, 9, 9)), 'image/jpeg')], kind='photo', category='front', url=self.url('tasin-vehicles'))
        main, gallery = Attachment.objects.get(kind='profile'), Attachment.objects.get(kind='photo')
        r = self.c.post(f'/api/v1/orgs/{self.org.pk}/files/{gallery.id}/make-primary')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Attachment.objects.get(pk=gallery.pk).kind, 'profile')
        self.assertEqual(Attachment.objects.get(pk=main.pk).kind, 'photo')

    def test_failed_processing_keeps_original_and_can_be_retried(self):
        with mock.patch('rentals.services.files.images.build_variants', side_effect=images.ImageRejected('the_image_could_not_be_decoded')):
            r = self.up([('a.jpg', jpeg(300, 300), 'image/jpeg')], kind='photo')
        self.assertEqual(r.status_code, 200)
        a = Attachment.objects.get()
        self.assertEqual(a.status, 'failed')
        self.assertTrue(get_storage().exists(a.storage_key))
        r = self.c.post(f'/api/v1/orgs/{self.org.pk}/files/{a.id}/retry')
        self.assertEqual(r.status_code, 200, r.content)
        a.refresh_from_db()
        self.assertEqual(a.status, 'completed')
        self.assertIn('thumb', a.variants)

    def test_update_description_and_category(self):
        self.up([('a.pdf', PDF, 'application/pdf')], category='license')
        a = Attachment.objects.get()
        r = self.c.patch(f'/api/v1/orgs/{self.org.pk}/files/{a.id}', data=json.dumps({'description': 'front side', 'category': 'passport'}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        a.refresh_from_db()
        self.assertEqual((a.description, a.category), ('front side', 'passport'))

    def test_every_entity_type_accepts_files_and_lists_them(self):
        for kind in ('tasin-drivers', 'tasin-owners', 'tasin-vehicles', 'tasin-agreements', 'tasin-maintenance', 'tasin-transactions'):
            r = self.up([(f'{kind}.pdf', PDF + kind.encode(), 'application/pdf')], url=self.url(kind))
            self.assertEqual(r.status_code, 200, (kind, r.content))
            lst = self.c.get(self.url(kind)).json()
            self.assertEqual(len(lst), 1, kind)
            self.assertEqual(lst[0]['fileName'], f'{kind}.pdf')

    def test_usage_overview(self):
        self.up([('a.jpg', jpeg(800, 800), 'image/jpeg')], kind='photo')
        self.up([('a.pdf', PDF, 'application/pdf')])
        u = self.c.get(f'/api/v1/orgs/{self.org.pk}/files-usage').json()
        self.assertEqual((u['files'], u['images'], u['documents']), (2, 1, 1))
        self.assertGreater(u['bytesTotal'], 0)
        self.assertEqual(u['byModule']['driver']['files'], 2)


class AccessTests(Base):
    def setUp(self):
        super().setUp()
        self.up([('a.jpg', jpeg(400, 300), 'image/jpeg')], kind='photo')
        self.att = Attachment.objects.get()

    def test_unauthenticated_is_401(self):
        c = Client()
        self.assertEqual(c.get(self.url()).status_code, 401)
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org.pk}/files/{self.att.id}/content').status_code, 401)
        self.assertEqual(self.up([('a.pdf', PDF, 'application/pdf')], client=c).status_code, 401)

    def test_other_organization_cannot_read_write_or_delete(self):
        u = User.objects.create_user('intruder', password='x')
        Membership.objects.create(organization=self.org2, user=u, role='admin', is_super_admin=True)
        c = Client(); c.force_login(u)
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org.pk}/files/{self.att.id}/content').status_code, 403)
        self.assertEqual(c.get(self.url()).status_code, 403)
        self.assertEqual(c.delete(f'/api/v1/orgs/{self.org.pk}/files/{self.att.id}').status_code, 403)
        # even inside their own org the id of another org's file resolves to nothing
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org2.pk}/files/{self.att.id}/content').status_code, 404)
        self.assertEqual(c.delete(f'/api/v1/orgs/{self.org2.pk}/files/{self.att.id}').status_code, 404)
        self.assertTrue(Attachment.objects.filter(pk=self.att.pk).exists())

    def test_member_without_the_module_permission_is_refused(self):
        u = User.objects.create_user('viewer', password='x')
        Membership.objects.create(organization=self.org, user=u, role='viewer')
        c = Client(); c.force_login(u)
        self.assertEqual(self.up([('a.pdf', PDF, 'application/pdf')], client=c).status_code, 403)
        self.assertEqual(c.delete(f'/api/v1/orgs/{self.org.pk}/files/{self.att.id}').status_code, 403)
        self.assertTrue(Attachment.objects.filter(pk=self.att.pk).exists())

    def test_entity_of_another_org_is_not_reachable_even_for_admin(self):
        other = Party.objects.create(organization=self.org2, kind='driver', name='Z')
        r = self.up([('a.pdf', PDF, 'application/pdf')], url=f'/api/v1/orgs/{self.org.pk}/tasin-drivers/{other.pk}/files')
        self.assertEqual(r.status_code, 404)

    def test_content_is_served_privately_with_safe_headers(self):
        r = self.c.get(f'/api/v1/orgs/{self.org.pk}/files/{self.att.id}/content?variant=thumb')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/webp')
        self.assertEqual(r['X-Content-Type-Options'], 'nosniff')
        self.assertIn('private', r['Cache-Control'])
        self.assertTrue(r['Content-Disposition'].startswith('inline'))
        d = self.c.get(f'/api/v1/orgs/{self.org.pk}/files/{self.att.id}/content?download=1')
        self.assertTrue(d['Content-Disposition'].startswith('attachment'))
        self.assertTrue(AuditEvent.objects.filter(action='attachment.download').exists())

    def test_listing_never_exposes_storage_keys(self):
        body = json.dumps(self.c.get(self.url()).json())
        self.assertNotIn('org-', body)
        self.assertNotIn(self.tmp.replace('\\', '/'), body)


class S3BackendTests(Base):
    def setUp(self):
        super().setUp()
        import boto3
        from moto import mock_aws
        self.mock = mock_aws()
        self.mock.start()
        boto3.client('s3', region_name='us-east-1').create_bucket(Bucket='tasin-test')
        self.s3 = override_settings(FILE_STORAGE_BACKEND='s3', S3_BUCKET='tasin-test', S3_REGION='us-east-1', S3_ENDPOINT_URL='', S3_ACCESS_KEY_ID='k',
                                    S3_SECRET_ACCESS_KEY='s', S3_KEY_PREFIX='tasin')
        self.s3.enable()
        reset_storage()

    def tearDown(self):
        reset_storage()
        self.s3.disable()
        self.mock.stop()
        super().tearDown()

    def test_upload_to_s3_signed_redirect_and_delete(self):
        r = self.up([('a.jpg', jpeg(900, 600), 'image/jpeg')], kind='photo')
        self.assertEqual(r.status_code, 200, r.content)
        a = Attachment.objects.get()
        self.assertEqual(a.storage_provider, 's3')
        self.assertTrue(get_storage().exists(a.storage_key))
        self.assertEqual(len(list(get_storage().iter_keys())), 3)
        resp = self.c.get(f'/api/v1/orgs/{self.org.pk}/files/{a.id}/content?variant=medium')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('X-Amz-Signature', resp['Location'])
        self.assertIn('X-Amz-Expires=300', resp['Location'])
        self.assertIn('no-store', resp['Cache-Control'])
        with self.captureOnCommitCallbacks(execute=True):
            self.c.delete(f'/api/v1/orgs/{self.org.pk}/files/{a.id}')
        self.assertEqual(list(get_storage().iter_keys()), [])

    def test_s3_outage_gives_503(self):
        self.mock.stop()
        with override_settings(S3_BUCKET='tasin-test'):
            reset_storage()
            r = self.up([('a.pdf', PDF, 'application/pdf')])
        self.assertIn(r.status_code, (503, 500))
        self.mock.start()
