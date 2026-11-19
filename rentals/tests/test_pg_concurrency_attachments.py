"""Attachment concurrency on REAL PostgreSQL (skipped on SQLite, where row locks do nothing).
Run against a disposable PostgreSQL / Neon branch, never production:
    DATABASE_URL=postgres://.../scratch DJANGO_DEBUG=1 python manage.py test rentals.tests.test_pg_concurrency_attachments --keepdb
"""
import io
import shutil
import tempfile
import threading
from datetime import date
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, close_old_connections
from django.test import TransactionTestCase, Client, override_settings
from PIL import Image
from rentals.models import Organization, Membership, Party, Vehicle, Attachment
from rentals.services.files.storage import get_storage, reset_storage

IS_PG = connection.vendor == 'postgresql'


def jpg(color, size=(320, 240)):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, 'JPEG', quality=90)
    return out.getvalue()


def run_parallel(n, fn):
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        try:
            c = Client()
            c.force_login(get_user_model().objects.get(username='att_pg_admin'))
            barrier.wait(timeout=30)
            results[i] = fn(i, c)
        except Exception as exc:
            results[i] = exc
        finally:
            close_old_connections()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join(120) for t in ts]
    return results


@skipUnless(IS_PG, 'needs PostgreSQL')
class AttachmentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ov = override_settings(PRIVATE_MEDIA_ROOT=self.tmp, FILE_STORAGE_BACKEND='filesystem')
        self.ov.enable()
        reset_storage()
        self.admin = get_user_model().objects.create_user('att_pg_admin', password='x')
        self.org = Organization.objects.create(name='AttPG')
        Membership.objects.create(organization=self.org, user=self.admin, role='admin', is_super_admin=True)
        self.driver = Party.objects.create(organization=self.org, kind='driver', name='D')
        self.owner = Party.objects.create(organization=self.org, kind='owner', name='O')
        self.car = Vehicle.objects.create(organization=self.org, registration='PG-1', model_name='m', owner=self.owner)

    def tearDown(self):
        reset_storage()
        self.ov.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, c, data, kind='document', idem=None, category='', entity='tasin-drivers', pk=None):
        extra = {'HTTP_IDEMPOTENCY_KEY': idem} if idem else {}
        pk = pk or {'tasin-drivers': self.driver.pk, 'tasin-vehicles': self.car.pk}[entity]
        return c.post(f'/api/v1/orgs/{self.org.pk}/{entity}/{pk}/files', {'files': [SimpleUploadedFile('f.jpg', data, content_type='image/jpeg')], 'kind': kind, 'category': category}, **extra)

    def stored_keys(self):
        return set(get_storage().iter_keys())

    def expected_keys(self):
        keys = set()
        for a in Attachment.objects.all():
            keys.update(a.all_keys())
        return keys

    def test_same_retry_key_from_many_requests_creates_one_attachment(self):
        blob = jpg((10, 120, 200))
        res = run_parallel(8, lambda i, c: self.post(c, blob, kind='photo', idem='retry-key-1').status_code)
        self.assertTrue(all(r == 200 for r in res), res)
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(self.stored_keys(), self.expected_keys())      # nothing orphaned by the losing requests

    def test_identical_file_with_different_keys_is_stored_once(self):
        blob = jpg((200, 20, 20))
        res = run_parallel(6, lambda i, c: self.post(c, blob, kind='photo', idem=f'k{i}').status_code)
        self.assertTrue(all(r == 200 for r in res), res)
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(self.stored_keys(), self.expected_keys())

    def test_concurrent_profile_pictures_leave_exactly_one_main_picture(self):
        res = run_parallel(6, lambda i, c: self.post(c, jpg((i * 40, 80, 255 - i * 40)), kind='profile', idem=f'p{i}').status_code)
        self.assertTrue(all(r in (200, 409) for r in res), res)          # a loser may be told to retry; nobody errors out
        self.assertEqual(Attachment.objects.filter(party=self.driver, kind='profile').count(), 1)
        self.assertEqual(self.stored_keys(), self.expected_keys())      # replaced pictures were removed from storage

    def test_concurrent_deletes_of_one_file_succeed_once(self):
        c0 = Client(); c0.force_login(self.admin)
        r = self.post(c0, jpg((1, 2, 3)), kind='photo')
        self.assertEqual(r.status_code, 200)
        fid = r.json()['items'][0]['id']
        res = run_parallel(5, lambda i, c: c.delete(f'/api/v1/orgs/{self.org.pk}/files/{fid}').status_code)
        self.assertEqual(res.count(200), 1, res)
        self.assertTrue(all(x in (200, 404) for x in res), res)
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(self.stored_keys(), set())

    def test_upload_racing_with_deleting_the_record_never_leaves_a_row(self):
        def work(i, c):
            if i == 0:
                self.car.delete()
                return 'deleted'
            return self.post(c, jpg((i, i, i)), kind='photo', entity='tasin-vehicles', idem=f'v{i}').status_code
        res = run_parallel(5, work)
        # Either the record was deleted (then no attachment may point at it) or it survived (then its attachments are legitimate): never a row without a record.
        if not Vehicle.objects.filter(pk=self.car.pk).exists():
            self.assertFalse(Attachment.objects.filter(vehicle_id=self.car.pk).exists())
        self.assertEqual(Attachment.objects.exclude(vehicle__isnull=False).count(), 0)
        self.assertEqual(self.stored_keys(), self.expected_keys())                      # and no storage object without a row

    def test_foreign_key_and_check_constraints_hold_on_postgres(self):
        from django.db import IntegrityError, transaction
        base = dict(organization=self.org, original_name='x', stored_name='x', mime_type='application/pdf', extension='pdf', sha256='0' * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attachment.objects.create(**base)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attachment.objects.create(party=self.driver, vehicle=self.car, **base)
        Attachment.objects.create(party=self.driver, kind='profile', **base)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attachment.objects.create(party=self.driver, kind='profile', **{**base, 'sha256': '1' * 64})      # second profile picture
        self.driver.delete()
        self.assertEqual(Attachment.objects.count(), 0)
