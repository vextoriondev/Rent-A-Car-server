import shutil
import tempfile
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from ..models import AuditEvent, Document, Expense, MaintenanceCase, Membership, Organization
from .. import services
from .base import RentalsTestCase

D=Decimal
PDF=b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF'
PNG=b'\x89PNG\r\n\x1a\n'+b'0'*32

class M8Base(RentalsTestCase):
    def setUp(self):
        super().setUp()
        self.media=tempfile.mkdtemp();self.override=override_settings(PRIVATE_MEDIA_ROOT=self.media);self.override.enable()
        U=get_user_model()
        self.users={}
        for role in('admin','operations','viewer','mechanic'):
            self.users[role]=U.objects.create_user(role,password='x');Membership.objects.create(organization=self.org,user=self.users[role],role=role)
        self.users['accountant']=self.user
        self.base=f'/api/v1/orgs/{self.org.pk}'

    def tearDown(self):
        self.override.disable();shutil.rmtree(self.media,ignore_errors=True)

    def as_role(self,role):
        c=Client();c.force_login(self.users[role]);return c

    def expense(self,ref='M8-1',amount='500',draft=False,**kw):
        data={'period_id':self.period.pk,'category':'fuel','amount':D(amount),'paid_by':'operator','responsible':'owner','offset_owner':False,'reference':ref,'event_date':date(2026,9,5),'draft':draft,**kw}
        return services.record_expense(self.org,self.user,data,ref)

    def upload(self,target_type,target_id,data=PDF,content_type='application/pdf',name='bill.pdf',role='accountant'):
        return services.upload_document(self.org,self.users[role],target_type,target_id,name,content_type,data,role)

    def actions(self):return list(AuditEvent.objects.filter(organization=self.org).values_list('action',flat=True))

class DocumentTests(M8Base):
    def test_upload_stores_privately_with_hash_and_sanitized_name(self):
        e=self.expense();d=self.upload('expense',e.pk,name='../../etc/evil bill!.pdf')
        import hashlib,os
        self.assertEqual((d.size,d.sha256,d.original_name,d.content_type),(len(PDF),hashlib.sha256(PDF).hexdigest(),'evil bill_.pdf','application/pdf'))
        self.assertTrue(os.path.exists(os.path.join(self.media,d.storage_key)));self.assertNotIn('..',d.storage_key)
        self.assertEqual(services.read_bytes(d),PDF)

    def test_upload_validation(self):
        e=self.expense()
        for data,ctype in((b'',  'application/pdf'),(PNG,'application/pdf'),(b'<html></html>','text/html'),(b'MZ'+b'0'*50,'application/pdf'),(PDF*1,'image/png')):
            with self.assertRaises(services.DomainError,msg=ctype):self.upload('expense',e.pk,data,ctype)
        with self.assertRaises(services.DomainError):self.upload('expense',e.pk,PDF+b'0'*(10*1024*1024),'application/pdf')
        with self.assertRaises(services.DomainError):self.upload('payroll',1)                               # unsupported target
        other=Organization.objects.create(name='Other')
        with self.assertRaises(Expense.DoesNotExist):services.upload_document(other,self.user,'expense',e.pk,'a.pdf','application/pdf',PDF)
        self.assertEqual(Document.objects.count(),0)

    def test_signed_url_is_user_bound_expiring_and_tamper_proof(self):
        d=self.upload('expense',self.expense().pk)
        member=Membership.objects.get(user=self.user);url=services.issue_url(self.org,member,self.user,d.pk)
        token=url['url'].split('token=')[1];self.assertEqual(services.resolve_token(token,self.user).pk,d.pk)
        with self.assertRaises(services.DomainError):services.resolve_token(token,self.users['admin'])        # issued to someone else
        with self.assertRaises(services.DomainError):services.resolve_token(token[:-2]+'xx',self.user)         # tampered
        with override_settings(DOCUMENT_URL_MAX_AGE=-1):
            with self.assertRaises(services.DomainError) as ctx:services.resolve_token(token,self.user)
        self.assertEqual(ctx.exception.code,'this_link_has_expired')

    def test_download_over_api_checks_token_user_and_org(self):
        d=self.upload('expense',self.expense().pk);c=self.as_role('accountant')
        url=c.get(f'{self.base}/documents/{d.pk}/url').json()['url']
        r=c.get('/api/v1'+url);self.assertEqual(r.status_code,200);self.assertEqual(b''.join(r.streaming_content),PDF)
        self.assertEqual((r['X-Content-Type-Options'],r['Cache-Control']),('nosniff','private, no-store'));self.assertIn('attachment',r['Content-Disposition'])
        self.assertEqual(self.as_role('admin').get('/api/v1'+url).status_code,403)                              # someone else's link
        self.assertEqual(Client().get('/api/v1'+url).status_code,401)                                           # anonymous
        outsider=get_user_model().objects.create_user('out',password='x');other=Organization.objects.create(name='O2');Membership.objects.create(organization=other,user=outsider,role='admin')
        oc=Client();oc.force_login(outsider)
        self.assertEqual(oc.get(f'/api/v1/orgs/{other.pk}/documents/{d.pk}/url').status_code,404)             # other org cannot even request one
        self.assertEqual(c.get(f'/api/v1/documents/{d.pk+99}/download?token={url.split("token=")[1]}').status_code,403)
        self.assertIn('document.access',self.actions())

    def test_removal_only_while_parent_is_draft(self):
        draft=self.expense('DR-1',draft=True);d1=self.upload('expense',draft.pk)
        services.delete_document(self.org,self.user,d1.pk);self.assertFalse(Document.objects.filter(pk=d1.pk).exists())
        posted=self.expense('PO-1','777');d2=self.upload('expense',posted.pk)
        with self.assertRaises(services.DomainError):services.delete_document(self.org,self.user,d2.pk)
        self.assertTrue(Document.objects.filter(pk=d2.pk).exists())
        self.assertEqual(self.as_role('accountant').delete(f'{self.base}/documents/{d2.pk}').status_code,422)

    def test_required_document_blocks_posting_until_attached(self):
        v2=services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{},'proof')
        services.set_expense_rule(self.org,self.user,v2.pk,'repair',responsible='owner',proof_required=True)
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        d=services.record_expense(self.org,self.user,{'period_id':october.pk,'category':'repair','amount':D(900),'paid_by':'operator','responsible':'owner','reference':'NEEDS-DOC','event_date':date(2026,10,3),'draft':True},'nd')
        with self.assertRaises(services.DomainError) as ctx:services.submit_expense(self.org,self.user,d.pk)
        self.assertEqual(ctx.exception.code,'a_receipt_is_required_for_this_category')
        self.upload('expense',d.pk)
        self.assertEqual(services.submit_expense(self.org,self.user,d.pk).state,'posted')

    def test_mechanic_documents_limited_to_maintenance_and_vehicles(self):
        e=self.expense()
        with self.assertRaises(services.DomainError):self.upload('expense',e.pk,role='mechanic')
        case=services.create_case(self.org,self.users['mechanic'],self.car.pk,'Brake noise')
        d=self.upload('maintenance_case',case.pk,PNG,'image/png','photo.png','mechanic')
        accountant_doc=self.upload('expense',e.pk)
        mc=self.as_role('mechanic')
        self.assertEqual(mc.get(f'{self.base}/documents/{d.pk}/url').status_code,200)
        self.assertEqual(mc.get(f'{self.base}/documents/{accountant_doc.pk}/url').status_code,422)             # not theirs to open
        self.assertEqual(self.as_role('viewer').get(f'{self.base}/documents/{accountant_doc.pk}/url').status_code,200)

    def test_api_multipart_upload_and_list(self):
        e=self.expense();c=self.as_role('accountant')
        r=c.post(f'{self.base}/documents',{'target_type':'expense','target_id':e.pk,'file':SimpleUploadedFile('r.pdf',PDF,content_type='application/pdf')})
        self.assertEqual(r.status_code,200,r.content);self.assertEqual(r.json()['name'],'r.pdf')
        self.assertEqual(len(c.get(f'{self.base}/documents?target_type=expense&target_id={e.pk}').json()),1)
        bad=c.post(f'{self.base}/documents',{'target_type':'expense','target_id':e.pk,'file':SimpleUploadedFile('r.pdf',PNG,content_type='application/pdf')});self.assertEqual(bad.status_code,422)

class AuditTests(M8Base):
    def test_one_row_per_state_change_with_before_after_and_reason(self):
        e=self.expense('A-1','6000');services.approve_expense(self.org,self.user,e.pk);services.reverse_expense(self.org,self.user,e.pk,'Wrong vehicle')
        ev={a.action:a for a in AuditEvent.objects.filter(target_type='expense',target_id=str(e.pk))}
        self.assertEqual(ev['expense.record'].after['state'],'pending')
        self.assertEqual((ev['expense.approve'].before['state'],ev['expense.approve'].after['state']),('pending','posted'))
        self.assertEqual((ev['expense.reverse'].before['state'],ev['expense.reverse'].after['state'],ev['expense.reverse'].reason),('posted','reversed','Wrong vehicle'))
        self.assertEqual(ev['expense.reverse'].actor,self.user);self.assertEqual(ev['expense.reverse'].after['amount'],'6000.00')

    def test_every_service_family_writes_audit(self):
        e=self.expense('F-1');self.pay('customer_payment','1000','AP-1')
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'customer','kind':'charge','amount':D(300)},'c1')
        ch=services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'customer','kind':'charge','amount':D(250)},'c2');inv=services.issue_invoice(self.org,self.user,{'period_id':self.period.pk,'obligation_ids':[ch.obligation_id]},'i1');services.void_invoice(self.org,self.user,inv.pk,'test')
        v2=services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'owner_rent':'61000'},'raise');services.set_expense_rule(self.org,self.user,v2.pk,'toll',responsible='customer');services.delete_expense_rule(self.org,self.user,v2.pk,'toll')
        d2=Party=None
        from ..models import Party as P
        new_driver=P.objects.create(organization=self.org,kind='driver',name='ND');services.assign_driver(self.org,self.user,self.agreement.pk,new_driver.pk,date(2026,11,1))
        new_owner=P.objects.create(organization=self.org,kind='owner',name='NO');services.transfer_vehicle_ownership(self.org,self.user,self.car.pk,new_owner.pk,date(2026,12,1))
        services.update_party(self.org,self.owner.pk,{'name':'Farhana R.'},self.user);services.set_consent(self.org,self.owner.pk,sms=True,preferred_channel='sms',source='form',actor=self.user)
        c=services.add_contact(self.org,self.customer.pk,{'name':'AP desk','role':'billing'},self.user);services.delete_contact(self.org,self.customer.pk,c.pk,self.user)
        get_user_model().objects.create_user('ownlogin',password='x');link=services.grant_party_access(self.org,self.owner.pk,'ownlogin',actor=self.user);services.revoke_party_access(self.org,self.owner.pk,link.user_id,self.user)
        services.create_branch(self.org,'Sylhet',self.user)
        services.update_settings(self.org,self.users['admin'],cutoff_day=28)
        self.pay('owner_payout','40000','AP-2');self.pay('driver_payout','20000','AP-3');self.pay('customer_payment','99000','AP-4')
        s=services.prepare_settlement(self.org,self.user,self.period.pk);services.request_settlement_approval(self.org,self.user,s.pk);services.approve_settlement(self.org,self.users['admin'],s.pk,'admin');services.close_settlement(self.org,self.user,s.pk)
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1),self.user);services.carry_forward(self.org,self.user,october.pk)
        services.reopen_settlement(self.org,self.users['admin'],s.pk,'check') if False else None
        case=services.create_case(self.org,self.user,self.car.pk,'AC');services.approve_case(self.org,self.user,case.pk);services.update_case(self.org,self.user,case.pk,status='in_progress')
        doc=self.upload('maintenance_case',case.pk);services.issue_url(self.org,Membership.objects.get(user=self.user),self.user,doc.pk)
        have=set(self.actions())
        want={'expense.record','payment.record','charge.post','period.carry_out','invoice.issue','invoice.void','agreement.revise','expense_rule.set','expense_rule.delete','agreement.assign_driver','vehicle.transfer_ownership','party.update','party.consent','party.contact_add','party.contact_delete','party.access_grant','party.access_revoke','branch.create','settings.update','settlement.prepare','settlement.request_approval','settlement.approve','settlement.close','period.issue','maintenance.create','maintenance.approve','maintenance.update','document.upload','document.access'}
        self.assertEqual(want-have,set())

    def test_reopen_and_late_state_changes_audited(self):
        self.pay('customer_payment','100000','R-1');self.pay('owner_payout','60000','R-2');self.pay('driver_payout','20000','R-3')
        s=services.prepare_settlement(self.org,self.user,self.period.pk);services.request_settlement_approval(self.org,self.user,s.pk);services.approve_settlement(self.org,self.users['admin'],s.pk,'admin');services.close_settlement(self.org,self.user,s.pk)
        services.reopen_settlement(self.org,self.users['admin'],s.pk,'Missed bill')
        ev=AuditEvent.objects.get(action='settlement.reopen');self.assertEqual((ev.reason,ev.before['state'],ev.after['state'],ev.actor),('Missed bill','closed','reopened',self.users['admin']))

    def test_replay_and_failed_calls_do_not_write_audit(self):
        self.expense('RP-1');n=AuditEvent.objects.count()
        self.expense('RP-1')                                                                                       # same idempotency key: replay
        self.assertEqual(AuditEvent.objects.count(),n)
        with self.assertRaises(services.DomainError):services.reverse_expense(self.org,self.user,Expense.objects.get(reference='RP-1').pk,'')
        self.assertEqual(AuditEvent.objects.count(),n)

    def test_events_are_append_only(self):
        self.expense('AO-1');ev=AuditEvent.objects.first()
        ev.reason='tampered'
        with self.assertRaises(RuntimeError):ev.save()
        with self.assertRaises(RuntimeError):ev.delete()

    def test_request_context_recorded_through_api(self):
        self.as_role('accountant').post(f'{self.base}/expenses',{'period_id':self.period.pk,'category':'fuel','amount':'100','event_date':'2026-09-06','paid_by':'operator','responsible':'owner','reference':'CTX'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='ctx',HTTP_X_REQUEST_ID='req-abc')
        ev=AuditEvent.objects.get(action='expense.record');self.assertEqual((ev.request_id,ev.ip),('req-abc','127.0.0.1'))

    def test_sensitive_reads_are_logged(self):
        self.as_role('accountant').get(f'{self.base}/agreements/{self.agreement.pk}');self.as_role('admin').get(f'{self.base}/parties')
        reads=list(AuditEvent.objects.filter(action='sensitive.read').values_list('target_type',flat=True))
        self.assertEqual(sorted(reads),['agreement','party_list'])

    def test_viewer_filters_pagination_and_permissions(self):
        for i in range(5):self.expense(f'V-{i}',str(100+i))
        a=self.as_role('accountant')
        r=a.get(f'{self.base}/audit-events?action=expense&limit=2').json();self.assertEqual((r['count'],len(r['items']),r['next']),(5,2,2))
        r2=a.get(f'{self.base}/audit-events?action=expense&limit=2&offset=4').json();self.assertEqual((len(r2['items']),r2['next']),(1,None))
        one=Expense.objects.get(reference='V-3');self.assertEqual(a.get(f'{self.base}/audit-events?target_type=expense&target_id={one.pk}').json()['count'],1)
        self.assertEqual(a.get(f'{self.base}/audit-events?action=payment').json()['count'],0)
        for role in('operations','viewer','mechanic'):self.assertEqual(self.as_role(role).get(f'{self.base}/audit-events').status_code,403,role)
        self.assertEqual(self.as_role('admin').get(f'{self.base}/audit-events').status_code,200)
        U=get_user_model();outsider=U.objects.create_user('o9',password='x');other=Organization.objects.create(name='Other');Membership.objects.create(organization=other,user=outsider,role='admin')
        oc=Client();oc.force_login(outsider);self.assertEqual(oc.get(f'/api/v1/orgs/{other.pk}/audit-events').json()['count'],0)

class MaintenanceTests(M8Base):
    def test_mechanic_can_request_but_not_approve_update_or_post_money(self):
        mc=self.as_role('mechanic')
        r=mc.post(f'{self.base}/maintenance-cases',{'vehicle_id':self.car.pk,'issue':'Engine light','quote':'4500'},content_type='application/json');self.assertEqual(r.status_code,200,r.content)
        cid=r.json()['id'];self.assertEqual((r.json()['status'],r.json()['quote']),('requested','4500.00'))
        self.assertEqual(mc.post(f'{self.base}/maintenance-cases/{cid}/approve').status_code,403)
        self.assertEqual(mc.patch(f'{self.base}/maintenance-cases/{cid}',{'status':'in_progress'},content_type='application/json').status_code,403)
        self.assertEqual(mc.post(f'{self.base}/expenses',{'period_id':self.period.pk,'category':'repair','amount':'100','event_date':'2026-09-06','paid_by':'operator','responsible':'owner','reference':'MX'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='mx').status_code,403)
        self.assertEqual(mc.post(f'{self.base}/payments',{'kind':'customer_payment','amount':'1','reference':'m','event_date':'2026-09-01','allocations':[]},content_type='application/json').status_code,403)
        self.assertEqual(Expense.objects.count(),0)

    def test_mechanic_sees_only_own_cases_staff_see_all(self):
        mine=services.create_case(self.org,self.users['mechanic'],self.car.pk,'Mine')
        services.create_case(self.org,self.user,self.car.pk,'Staff-opened')
        self.assertEqual([c['id'] for c in self.as_role('mechanic').get(f'{self.base}/maintenance-cases').json()['items']],[mine.pk])
        self.assertEqual(len(self.as_role('operations').get(f'{self.base}/maintenance-cases').json()['items']),2)
        own=get_user_model().objects.create_user('own5',password='x');services.grant_party_access(self.org,self.owner.pk,'own5');oc=Client();oc.force_login(own)
        self.assertEqual(oc.get(f'{self.base}/maintenance-cases').status_code,403)

    def test_status_flow_and_approval(self):
        case=services.create_case(self.org,self.user,self.car.pk,'AC repair',quote='7000',downtime_from=date(2026,9,10),downtime_to=date(2026,9,12))
        with self.assertRaises(services.DomainError):services.update_case(self.org,self.user,case.pk,status='done')          # must be approved first
        with self.assertRaises(services.DomainError):services.update_case(self.org,self.user,case.pk,status='approved')       # separate action
        case=services.approve_case(self.org,self.users['admin'],case.pk);self.assertEqual((case.status,case.approved_by),('approved',self.users['admin']))
        with self.assertRaises(services.DomainError):services.approve_case(self.org,self.user,case.pk)
        services.update_case(self.org,self.user,case.pk,status='in_progress');services.update_case(self.org,self.user,case.pk,status='done')
        with self.assertRaises(services.DomainError):services.update_case(self.org,self.user,case.pk,vendor='X')              # closed
        with self.assertRaises(services.DomainError):services.create_case(self.org,self.user,self.car.pk,'x',downtime_from=date(2026,9,5),downtime_to=date(2026,9,1))

    def test_expense_links_to_approved_case_of_same_vehicle_only(self):
        case=services.create_case(self.org,self.user,self.car.pk,'Brakes')
        with self.assertRaises(services.DomainError):self.expense('MC-1',maintenance_case_id=case.pk)                          # not approved yet
        services.approve_case(self.org,self.user,case.pk)
        e=self.expense('MC-2',maintenance_case_id=case.pk);self.assertEqual(e.maintenance_case_id,case.pk)
        from ..models import Vehicle
        other_car=Vehicle.objects.create(organization=self.org,registration='X-9',model_name='M',owner=self.owner)
        other=services.approve_case(self.org,self.user,services.create_case(self.org,self.user,other_car.pk,'Other').pk)
        with self.assertRaises(services.DomainError):self.expense('MC-3',maintenance_case_id=other.pk)
        self.assertEqual(self.as_role('accountant').get(f'{self.base}/maintenance-cases?vehicle_id={self.car.pk}').json()['items'][0]['id'],case.pk)
        self.assertEqual(services.statement(self.period)['owner']['separate_recoverable'],D(500))                          # money still only via the expense engine

    def test_other_organization_cases_invisible(self):
        case=services.create_case(self.org,self.user,self.car.pk,'Mine')
        other=Organization.objects.create(name='O3');U=get_user_model().objects.create_user('o3',password='x');Membership.objects.create(organization=other,user=U,role='admin')
        c=Client();c.force_login(U)
        self.assertEqual(c.get(f'/api/v1/orgs/{other.pk}/maintenance-cases').json()['items'],[])
        self.assertEqual(c.post(f'/api/v1/orgs/{other.pk}/maintenance-cases/{case.pk}/approve').status_code,404)
