import csv
import io
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client
from ..models import Agreement, Branch, Document, Expense, MaintenanceCase, Membership, NotificationOutbox, Party, Vehicle
from .. import services
from .base import RentalsTestCase

D=Decimal

class BranchBase(RentalsTestCase):
    """Main holds the worked fixture (customer 100,000 / owner 60,000 / driver 20,000); Sylhet holds a second fleet. `self.user` is org-wide (no branch)."""
    def setUp(self):
        super().setUp()
        self.base=f'/api/v1/orgs/{self.org.pk}'
        self.main=services.default_branch(self.org);self.sylhet=services.create_branch(self.org,'Sylhet',self.user)
        U=get_user_model()
        self.users={}
        for name,role,branch in(('acc_main','accountant',self.main),('acc_syl','accountant',self.sylhet),('admin_main','admin',self.main),('mech_syl','mechanic',self.sylhet),('view_main','viewer',self.main)):
            self.users[name]=U.objects.create_user(name,password='x');Membership.objects.create(organization=self.org,user=self.users[name],role=role,branch=branch)
        self.users['admin_all']=U.objects.create_user('admin_all',password='x');Membership.objects.create(organization=self.org,user=self.users['admin_all'],role='admin')
        self.syl=self.sylhet_fleet()

    def sylhet_fleet(self):
        o=Party.objects.create(organization=self.org,kind='owner',name='Syl Owner');cu=Party.objects.create(organization=self.org,kind='customer',name='Syl Customer');d=Party.objects.create(organization=self.org,kind='driver',name='Syl Driver')
        car=Vehicle.objects.create(organization=self.org,registration='SYL-1',model_name='Honda',owner=o,branch=self.sylhet)
        a,_=services.create_agreement(self.org,self.user,Agreement(organization=self.org,vehicle=car,customer=cu,driver=d,start_date=date(2026,1,1),customer_rent=D(50000),owner_rent=D(30000),driver_salary=D(10000)))
        p=services.issue_period(self.org,a.pk,date(2026,9,1));ob={x.kind:x for x in p.obligations.all()}
        e=services.record_expense(self.org,self.user,{'period_id':p.pk,'category':'repair','amount':D(1000),'paid_by':'operator','responsible':'owner','reference':'SYL-EXP','event_date':date(2026,9,3)},'syl-e')
        pay=services.record_payment(self.org,self.user,{'kind':'customer_payment','amount':D(20000),'reference':'SYL-PAY','method':'bank','event_date':date(2026,9,20),'allocations':[{'obligation_id':ob['customer_rent'].pk,'amount':D(20000)}]},'syl-p')
        notice=NotificationOutbox.objects.create(organization=self.org,event_key='syl-n',recipient=cu,event_type='period_closed',payload={})
        return {'owner':o,'customer':cu,'driver':d,'car':car,'agreement':a,'period':p,'ob':ob,'expense':e,'payment':pay,'notice':notice}

    def login(self,name=None):
        c=Client();c.force_login(self.users[name] if name else self.user);return c

    def get(self,name,path):return self.login(name).get(f'{self.base}{path}')
    def ids(self,name,path):
        r=self.get(name,path);self.assertEqual(r.status_code,200,(path,r.content));return {x['id'] for x in r.json()['items']}

class DataModelTests(BranchBase):
    def test_vehicles_and_agreements_default_to_main_and_agreement_follows_vehicle(self):
        self.assertEqual(self.car.branch_id,self.main.pk);self.assertEqual(self.agreement.branch_id,self.main.pk)
        self.assertEqual(self.syl['agreement'].branch_id,self.sylhet.pk)

    def test_agreement_branch_must_equal_vehicle_branch(self):
        car=Vehicle.objects.create(organization=self.org,registration='X-1',model_name='M',owner=self.owner,branch=self.main)
        bad=Agreement(organization=self.org,vehicle=car,customer=self.customer,driver=self.driver,branch=self.sylhet,start_date=date(2026,1,1),customer_rent=D(10),owner_rent=D(5),driver_salary=D(1))
        with self.assertRaisesMessage(services.DomainError,'same branch'):services.create_agreement(self.org,self.user,bad)
        r=self.login('acc_main').post(f'{self.base}/agreements',{'vehicle_id':car.pk,'customer_id':self.customer.pk,'driver_id':self.driver.pk,'start_date':'2026-01-01','customer_rent':'10','owner_rent':'5','driver_salary':'1','branch_id':self.sylhet.pk},content_type='application/json')
        self.assertEqual(r.status_code,422)

    def test_new_vehicle_branch_is_forced_for_limited_members_and_free_for_admins(self):
        body={'registration':'N-1','model_name':'M','owner_id':self.owner.pk,'branch_id':self.sylhet.pk}
        r=self.login('admin_main').post(f'{self.base}/vehicles',body,content_type='application/json');self.assertEqual(r.json()['branch_id'],self.sylhet.pk)
        r=self.login('acc_main').post(f'{self.base}/vehicles',{**body,'registration':'N-2'},content_type='application/json')
        self.assertEqual(r.status_code,403)  # accountants cannot create vehicles at all
        Membership.objects.filter(user=self.users['acc_main']).update(role='operations')
        r=self.login('acc_main').post(f'{self.base}/vehicles',{**body,'registration':'N-3'},content_type='application/json');self.assertEqual(r.json()['branch_id'],self.main.pk)
        self.assertEqual(self.login('admin_all').post(f'{self.base}/vehicles',{**body,'registration':'N-4','branch_id':999999},content_type='application/json').status_code,422)

class ScopeTests(BranchBase):
    def test_limited_accountant_sees_only_own_branch_in_every_list(self):
        main_only={'vehicles':{self.car.pk},'agreements':{self.agreement.pk},'periods':{self.period.pk}}
        for path,want in main_only.items():self.assertEqual(self.ids('acc_main','/'+path),want,path)
        for path,want in {'vehicles':{self.syl['car'].pk},'agreements':{self.syl['agreement'].pk},'periods':{self.syl['period'].pk}}.items():self.assertEqual(self.ids('acc_syl','/'+path),want,path)
        self.assertEqual(len(self.ids('admin_all','/vehicles')),2);self.assertEqual(len(self.ids(None,'/vehicles')),2)  # an org-wide member with no branch sees both

    def test_money_lists_are_scoped(self):
        e_main=services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'fuel','amount':D(500),'paid_by':'operator','responsible':'owner','reference':'M-EXP','event_date':date(2026,9,4)},'m-e')
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'customer','kind':'charge','amount':D(100)},'m-c')
        services.post_charge(self.org,self.user,{'period_id':self.syl['period'].pk,'side':'customer','kind':'charge','amount':D(100)},'s-c')
        inv_m=services.issue_invoice(self.org,self.user,{'period_id':self.period.pk},'m-i');inv_s=services.issue_invoice(self.org,self.user,{'period_id':self.syl['period'].pk},'s-i')
        services.prepare_settlement(self.org,self.user,self.period.pk);services.prepare_settlement(self.org,self.user,self.syl['period'].pk)
        self.assertEqual(self.ids('acc_main','/expenses'),{e_main.pk});self.assertEqual(self.ids('acc_syl','/expenses'),{self.syl['expense'].pk})
        self.assertEqual(self.ids('acc_main','/payments'),set());self.assertEqual(self.ids('acc_syl','/payments'),{self.syl['payment'].pk})
        self.assertEqual(len(self.ids('acc_main','/charges')),1);self.assertEqual(self.ids('acc_syl','/invoices'),{inv_s.pk});self.assertEqual(self.ids('acc_main','/invoices'),{inv_m.pk})
        self.assertEqual(len(self.ids('acc_main','/settlements')),1);self.assertIn(self.syl['notice'].pk,self.ids('acc_syl','/notifications'))
        main_notices=self.ids('acc_main','/notifications');self.assertTrue(main_notices);self.assertFalse(main_notices&self.ids('acc_syl','/notifications'))
        self.assertEqual(self.get('acc_main','/expenses').json()['totals']['count'],1)

    def test_parties_follow_agreements_plus_unlinked_ones_the_user_created(self):
        names=lambda who:{p['name'] for p in self.get(who,'/parties').json()['items']}
        self.assertEqual(names('acc_syl'),{'Syl Owner','Syl Customer','Syl Driver'})
        self.assertEqual(names('acc_main'),{'Farhana Rahman','Imran Hossain','Meridian Textiles Ltd.'})
        r=self.login('acc_syl').post(f'{self.base}/parties',{'kind':'customer','name':'Fresh Co'},content_type='application/json');self.assertEqual(r.status_code,200)
        self.assertIn('Fresh Co',names('acc_syl'));self.assertNotIn('Fresh Co',names('acc_main'));self.assertIn('Fresh Co',names('admin_all'))

    def test_admin_branch_filter_and_limited_users_cannot_widen_it(self):
        self.assertEqual(self.ids('admin_all','/agreements'),{self.agreement.pk,self.syl['agreement'].pk})
        self.assertEqual(self.ids('admin_all',f'/agreements?branch_id={self.sylhet.pk}'),{self.syl['agreement'].pk})
        self.assertEqual(self.ids('admin_main','/agreements'),{self.agreement.pk,self.syl['agreement'].pk})  # an admin is never confined, even with a branch
        self.assertEqual(self.ids('acc_main',f'/agreements?branch_id={self.sylhet.pk}'),{self.agreement.pk})
        self.assertEqual(self.ids('acc_main',f'/expenses?branch_id={self.sylhet.pk}'),set())
        self.assertEqual(self.ids('admin_all',f'/expenses?branch_id={self.main.pk}'),set())
        self.assertEqual(self.ids('admin_all',f'/payments?branch_id={self.sylhet.pk}'),{self.syl['payment'].pk})

    def test_mechanic_sees_only_own_branch_vehicles_and_cases(self):
        self.assertEqual(self.ids('mech_syl','/vehicles'),{self.syl['car'].pk})
        r=self.login('mech_syl').post(f'{self.base}/maintenance-cases',{'vehicle_id':self.syl['car'].pk,'issue':'Brake'},content_type='application/json');self.assertEqual(r.status_code,200)
        self.assertEqual(self.login('mech_syl').post(f'{self.base}/maintenance-cases',{'vehicle_id':self.car.pk,'issue':'Brake'},content_type='application/json').status_code,404)
        self.assertEqual(len(self.ids('acc_syl','/maintenance-cases')),1);self.assertEqual(self.ids('acc_main','/maintenance-cases'),set())

    def test_snapshot_is_scoped_and_admin_can_pick_a_branch(self):
        snap=lambda who,q='':self.get(who,f'/snapshot?month=2026-09-01{q}').json()
        self.assertEqual([a['registration'] for a in snap('acc_main')['agreements']],['DHA-19-7724'])
        self.assertEqual([a['registration'] for a in snap('acc_syl')['agreements']],['SYL-1'])
        self.assertEqual(len(snap('admin_all')['agreements']),2)
        self.assertEqual([a['registration'] for a in snap('admin_all',f'&branch_id={self.sylhet.pk}')['agreements']],['SYL-1'])
        self.assertEqual({n['recipient'] for n in snap('acc_main')['notices']},{'Meridian Textiles Ltd.'});self.assertTrue({n['recipient'] for n in snap('acc_syl')['notices']}<={'Syl Customer','Syl Owner','Syl Driver'})

class CrossBranchTests(BranchBase):
    def test_other_branch_ids_answer_404(self):
        p=self.syl['period'].pk;a=self.syl['agreement'].pk
        for path in(f'/periods/{p}/statement',f'/periods/{p}/ledger',f'/periods/{p}/obligations',f'/periods/{p}/settlement-preview',f'/periods/{p}/invoice-preview',f'/agreements/{a}'):
            self.assertEqual(self.get('acc_main',path).status_code,404,path);self.assertEqual(self.get('acc_syl',path).status_code,200,path)
        c=self.login('acc_main');post=lambda path,body=None:c.post(f'{self.base}{path}',body or {},content_type='application/json')
        self.assertEqual(post(f'/periods/{p}/close').status_code,404)
        self.assertEqual(post(f'/periods/{p}/settlement').status_code,404)
        self.assertEqual(post(f'/periods/{p}/carry-forward').status_code,404)
        self.assertEqual(post(f'/agreements/{a}/periods?month=2026-10-01').status_code,404)
        self.assertEqual(post(f'/expenses/{self.syl["expense"].pk}/approve').status_code,404)
        self.assertEqual(post(f'/expenses/{self.syl["expense"].pk}/reverse',{'reason':'x'}).status_code,404)
        self.assertEqual(post('/charges',{'period_id':p,'side':'customer','kind':'charge','amount':'5'}).status_code,404)
        self.assertEqual(post('/invoices',{'period_id':p}).status_code,404)
        self.assertEqual(post(f'/notifications/{self.syl["notice"].pk}/retry').status_code,404)
        self.assertEqual(post(f'/vehicles/{self.syl["car"].pk}/transfer-ownership',{'owner_id':self.owner.pk,'effective_from':'2026-12-01'}).status_code,404)
        self.assertEqual(post('/expenses',{'period_id':p,'category':'fuel','amount':'10','event_date':'2026-09-05','paid_by':'operator','responsible':'owner','reference':'X'}).status_code,404)
        self.assertEqual(post('/payments',{'kind':'customer_payment','amount':'5','reference':'XP','event_date':'2026-09-05','allocations':[{'obligation_id':self.syl['ob']['customer_rent'].pk,'amount':'5'}]}).status_code,404)
        self.assertEqual(Expense.objects.get(pk=self.syl['expense'].pk).state,self.syl['expense'].state)  # nothing changed

    def test_own_branch_writes_still_work_and_same_org_admin_is_unaffected(self):
        c=self.login('acc_syl')
        r=c.post(f'{self.base}/expenses',{'period_id':self.syl['period'].pk,'category':'fuel','amount':'10','event_date':'2026-09-05','paid_by':'operator','responsible':'owner','reference':'OWN-1'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='own-1');self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(self.login('admin_all').get(f'{self.base}/periods/{self.syl["period"].pk}/statement').status_code,200)

    def test_documents_follow_the_branch_of_their_target(self):
        doc=Document.objects.create(organization=self.org,target_type='expense',target_id=str(self.syl['expense'].pk),storage_key='k',original_name='r.pdf',content_type='application/pdf',size=1,sha256='0',uploaded_by=self.user)
        q=f'/documents?target_type=expense&target_id={self.syl["expense"].pk}'
        self.assertEqual(self.get('acc_main',q).status_code,404);self.assertEqual(len(self.get('acc_syl',q).json()),1)
        self.assertEqual(self.get('acc_main',f'/documents/{doc.pk}/url').status_code,422)
        self.assertEqual(self.get('acc_syl',f'/documents/{doc.pk}/url').status_code,200)

    def test_audit_log_is_closed_to_branch_limited_members(self):
        self.assertEqual(self.get('acc_main','/audit-events').status_code,403);self.assertEqual(self.get('admin_all','/audit-events').status_code,200)

class ReportScopeTests(BranchBase):
    def test_reports_and_exports_are_branch_scoped_and_echo_the_branch(self):
        r=self.get('acc_main','/reports/customer-balances').json();self.assertEqual([row[1] for row in r['rows']],['DHA-19-7724']);self.assertEqual(r['filters']['branch_id'],self.main.pk)
        r=self.get('acc_syl',f'/reports/customer-balances?branch_id={self.main.pk}').json();self.assertEqual([row[1] for row in r['rows']],['SYL-1']);self.assertEqual(r['filters']['branch_id'],self.sylhet.pk)
        all_=self.get('admin_all','/reports/customer-balances').json();self.assertEqual(all_['count'],2);self.assertNotIn('branch_id',all_['filters'])
        one=self.get('admin_all',f'/reports/customer-balances?branch_id={self.sylhet.pk}').json();self.assertEqual(one['count'],1)
        for who,want in(('acc_main','DHA-19-7724'),('acc_syl','SYL-1')):
            for rtype in('income','vehicle-contribution','owner-liability'):
                rows=self.get(who,f'/reports/{rtype}').json()['rows'];self.assertEqual(len(rows),1,(who,rtype));self.assertEqual(rows[0][1],want)
        self.assertEqual([r[0] for r in self.get('acc_main','/reports/customer-aging').json()['rows']],['Meridian Textiles Ltd.']);self.assertEqual([r[0] for r in self.get('acc_syl','/reports/customer-aging').json()['rows']],['Syl Customer'])
        self.assertEqual(self.get('admin_all','/reports/customer-aging').json()['count'],2)
        csv_=self.get('view_main','/reports/customer-balances/export');self.assertEqual(csv_.status_code,200)
        text=csv_.content.decode('utf-8');self.assertTrue(text.startswith('﻿'));self.assertIn(f'branch_id={self.main.pk}',text);self.assertNotIn('SYL-1',text)
        rows=list(csv.reader(io.StringIO(text.lstrip('﻿'))));self.assertEqual(sum(1 for r in rows if r and r[0].startswith('AGR-')),1)

    def test_entries_report_hides_other_branch_payments_and_expenses(self):
        self.pay('customer_payment','1000','M-PAY');services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'fuel','amount':D(300),'paid_by':'operator','responsible':'owner','reference':'M-EXP','event_date':date(2026,9,3)},'m-e')
        main=self.get('acc_main','/reports/entries').json()['rows'];syl=self.get('acc_syl','/reports/entries').json()['rows']
        self.assertTrue(main);self.assertTrue(syl);self.assertFalse({r[0] for r in main}&{r[0] for r in syl})
        self.assertIn(self.syl['payment'].public_id,{r[0] for r in syl})
        self.assertEqual(self.get('acc_main','/reports/daily-expenses?date=2026-09-03').json()['count'],1);self.assertEqual(self.get('acc_syl','/reports/daily-expenses?date=2026-09-03').json()['count'],1)
        self.assertEqual(self.get('admin_all','/reports/daily-expenses?date=2026-09-03').json()['count'],2)

    def test_worked_example_still_reconciles_in_the_default_branch(self):
        self.pay('customer_payment','70000','R-C');self.pay('owner_payout','48000','R-O')
        s=self.get('acc_main',f'/periods/{self.period.pk}/statement').json()
        self.assertEqual(s['customer']['billed'],'100000.00');self.assertEqual(s['customer']['due'],'30000.00')
        self.assertEqual(s['owner']['due'],'12000.00')
        self.assertEqual(self.get('acc_main',f'/periods/{self.period.pk}/ledger').json()['reconciles'],True)

class MemberAdminTests(BranchBase):
    def test_members_list_and_patch_are_admin_only_and_audited(self):
        self.assertEqual(self.get('acc_main','/members').status_code,403)
        rows=self.get('admin_all','/members').json();self.assertTrue({'acc_main','acc_syl','admin_all'}<={r['username'] for r in rows})
        row=next(r for r in rows if r['username']=='acc_main');self.assertEqual(row['branch']['name'],'Main');self.assertEqual(row['role'],'accountant')
        c=self.login('admin_all')
        r=c.patch(f'{self.base}/members/{row["id"]}',{'branch_id':self.sylhet.pk,'role':'operations'},content_type='application/json')
        self.assertEqual(r.status_code,200,r.content);self.assertEqual(r.json()['branch']['name'],'Sylhet');self.assertEqual(r.json()['role'],'operations')
        self.assertEqual(self.ids('acc_main','/agreements'),{self.syl['agreement'].pk})  # the change takes effect on the next request
        r=c.patch(f'{self.base}/members/{row["id"]}',{'clear_branch':True},content_type='application/json');self.assertIsNone(r.json()['branch'])
        self.assertEqual(len(self.ids('acc_main','/agreements')),2)
        from ..models import AuditEvent
        self.assertEqual(AuditEvent.objects.filter(action='member.update').count(),2)

    def test_last_admin_cannot_be_demoted_and_bad_input_is_rejected(self):
        admins=list(Membership.objects.filter(organization=self.org,role='admin'));c=self.login('admin_all')
        for m in admins[:-1]:self.assertEqual(c.patch(f'{self.base}/members/{m.pk}',{'role':'viewer'},content_type='application/json').status_code,200)
        last=admins[-1];r=c.patch(f'{self.base}/members/{last.pk}',{'role':'viewer'},content_type='application/json');self.assertEqual(r.status_code,422)
        self.assertEqual(Membership.objects.get(pk=last.pk).role,'admin')
        self.assertEqual(c.patch(f'{self.base}/members/{last.pk}',{'role':'owner'},content_type='application/json').status_code,422)  # not a grantable role
        self.assertEqual(c.patch(f'{self.base}/members/{last.pk}',{'branch_id':987654},content_type='application/json').status_code,422)
        self.assertEqual(c.patch(f'{self.base}/members/987654',{'role':'viewer'},content_type='application/json').status_code,404)

    def test_members_of_other_organizations_are_not_reachable(self):
        from ..models import Organization
        other=Organization.objects.create(name='O2');u=get_user_model().objects.create_user('o2',password='x');m=Membership.objects.create(organization=other,user=u,role='viewer')
        self.assertEqual(self.login('admin_all').patch(f'{self.base}/members/{m.pk}',{'role':'admin'},content_type='application/json').status_code,404)

    def test_party_access_endpoints_are_gone(self):
        c=self.login('admin_all')
        r=c.post(f'{self.base}/parties/{self.owner.pk}/access',{'username':'x'},content_type='application/json');self.assertEqual(r.status_code,410);self.assertIn('M10',r.json()['detail'])
        self.assertEqual(c.delete(f'{self.base}/parties/{self.owner.pk}/access/1').status_code,410)

    def test_me_names_the_branch_and_branches_list_is_narrowed(self):
        org=self.login('acc_syl').get('/api/v1/me').json()['organizations'][0];self.assertEqual(org['branch'],{'id':self.sylhet.pk,'name':'Sylhet'})
        self.assertEqual([b['name'] for b in self.get('acc_syl','/branches').json()],['Sylhet']);self.assertEqual(len(self.get('admin_all','/branches').json()),2)
