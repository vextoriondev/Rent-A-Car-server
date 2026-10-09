"""M16a-3: NID and driving licence numbers are admin-only; everyone else sees a masked value or nothing, and the values never leave the ids endpoint."""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from ..models import AuditEvent, Membership, NotificationOutbox, Party
from .. import services
from .test_reports import ReportBase

NID='1987654321098';LIC='DHA-1234567'

class ProtectedIdTests(ReportBase):
    def as_role(self,role):
        u=get_user_model().objects.create_user(f'u-{role}',password='x');Membership.objects.create(organization=self.org,user=u,role=role)
        c=Client();c.force_login(u);return c

    def setUp(self):
        super().setUp()
        self.admin=self.as_role('admin')
        r=self.admin.put(f'{self.base}/parties/{self.driver.pk}/ids',data={'nid_number':NID,'driving_licence_number':LIC},content_type='application/json')
        self.assertEqual(r.status_code,200,r.content)
        self.url=f'{self.base}/parties/{self.driver.pk}/ids'

    def test_admin_reads_full_values_and_the_read_is_logged(self):
        r=self.admin.get(self.url);self.assertEqual(r.status_code,200)
        self.assertEqual((r.json()['nid_number'],r.json()['driving_licence_number'],r.json()['masked']),(NID,LIC,False))
        e=AuditEvent.objects.filter(action='sensitive.read',target_type='party_ids',target_id=str(self.driver.pk)).first();self.assertIsNotNone(e)

    def test_finance_roles_see_last_four_only_and_other_roles_see_nothing(self):
        r=self.c.get(self.url);self.assertEqual(r.status_code,200)                      # the fixture user is an accountant
        j=r.json()
        self.assertTrue(j['nid_number'].endswith('1098') and NID not in j['nid_number'] and j['masked'])
        self.assertTrue(j['driving_licence_number'].endswith('4567') and LIC not in j['driving_licence_number'])
        for role in('operations','viewer','mechanic'):self.assertEqual(self.as_role(role).get(self.url).status_code,403,role)
        self.assertTrue(AuditEvent.objects.filter(action='sensitive.read',target_type='party_ids',actor=self.user).exists())

    def test_only_an_admin_can_write_ids_and_the_audit_keeps_no_full_value(self):
        self.assertEqual(self.c.put(self.url,data={'nid_number':'1'},content_type='application/json').status_code,403)
        e=AuditEvent.objects.filter(action='party.ids_update').first();self.assertIsNotNone(e)
        self.assertNotIn(NID,str(e.before)+str(e.after));self.assertNotIn(LIC,str(e.before)+str(e.after))
        r=self.admin.put(self.url,data={'nid_number':''},content_type='application/json');self.assertEqual(r.status_code,200)
        self.assertEqual(self.admin.get(self.url).json()['nid_number'],'');self.assertEqual(self.admin.get(self.url).json()['driving_licence_number'],LIC)   # only the sent field changes

    def test_values_are_in_no_list_snapshot_report_export_or_notice(self):
        for path in('/parties','/snapshot?month=2026-09-01','/reports/driver-payroll','/reports/driver-payroll/export','/reports/entries','/agreements','/notifications','/audit'):
            for client in(self.admin,self.c):
                r=client.get(f'{self.base}{path}');body=r.content.decode('utf8','replace')
                self.assertNotIn(NID,body,path);self.assertNotIn(LIC,body,path)
        services.set_consent(self.org,self.driver.pk,sms=True,actor=self.user)
        services.notice(self.org,'ids-test',Party.objects.get(pk=self.driver.pk),'payment_recorded',{'payment_id':'x'})
        self.assertNotIn(NID,str(list(NotificationOutbox.objects.values_list('payload',flat=True))))

    def test_identity_documents_are_visible_to_admins_only(self):
        pdf=SimpleUploadedFile('nid.pdf',b'%PDF-1.4 id scan',content_type='application/pdf')
        r=self.admin.post(f'{self.base}/documents',data={'target_type':'party','target_id':self.driver.pk,'category':'identity','file':pdf});self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r.json()['category'],'identity');doc=r.json()['id']
        self.assertEqual(len(self.admin.get(f'{self.base}/documents?target_type=party&target_id={self.driver.pk}').json()),1)
        self.assertEqual(self.c.get(f'{self.base}/documents?target_type=party&target_id={self.driver.pk}').json(),[])
        self.assertEqual(self.c.get(f'{self.base}/documents/{doc}/url').status_code,422)
        self.assertEqual(self.admin.get(f'{self.base}/documents/{doc}/url').status_code,200)
        other=self.c.post(f'{self.base}/documents',data={'target_type':'party','target_id':self.driver.pk,'category':'identity','file':SimpleUploadedFile('n.pdf',b'%PDF-1.4 x',content_type='application/pdf')})
        self.assertEqual(other.status_code,422)                                                       # an accountant cannot file identity papers

    def test_an_ordinary_party_document_stays_visible_to_finance(self):
        r=self.admin.post(f'{self.base}/documents',data={'target_type':'party','target_id':self.driver.pk,'file':SimpleUploadedFile('c.pdf',b'%PDF-1.4 contract',content_type='application/pdf')})
        self.assertEqual(r.status_code,200,r.content);self.assertEqual(r.json()['category'],'')
        self.assertEqual(len(self.c.get(f'{self.base}/documents?target_type=party&target_id={self.driver.pk}').json()),1)
