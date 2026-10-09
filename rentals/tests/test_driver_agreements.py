"""M16b: a standalone driver agreement (salary and staff) made outside any vehicle agreement."""
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client
from ..models import AuditEvent, Branch, DriverAgreement, Membership, Obligation, Organization, Party, Period
from .. import services
from .test_reports import ReportBase

D=Decimal

class DriverAgreementTests(ReportBase):
    def setUp(self):
        super().setUp()
        self.staff=Party.objects.create(organization=self.org,kind='driver',name='Solo Driver')

    def make(self,salary=12000,start=date(2026,9,1),end=None,driver=None,**kw):
        da=DriverAgreement(organization=self.org,driver=driver or self.staff,start_date=start,end_date=end,salary=D(salary),**kw)
        return services.create_driver_agreement(self.org,self.user,da)

    def issue(self,da,month=date(2026,9,1)):return services.issue_driver_period(self.org,da.pk,month,self.user)

    def pay_driver(self,period,amount,ref,kind='driver_payout'):
        ob=period.obligations.get(kind='driver_salary')
        return services.record_payment(self.org,self.user,{'kind':kind,'amount':D(amount),'reference':ref,'method':'bank','event_date':date(2026,9,20),'allocations':[{'obligation_id':ob.pk,'amount':D(amount)}]},ref)

    def reconciles(self,period):
        self.assertEqual(services.diff_statements(services.ledger_statement(period),services.obligation_statement(period)),[])

    # ---- creation and validation
    def test_create_writes_version_one_and_audits(self):
        da,warnings=self.make()
        self.assertEqual((da.status,da.active,da.branch_id is not None),('active',True,True))
        v=da.versions.get();self.assertEqual((v.number,v.salary,v.effective_from),(1,D(12000),date(2026,9,1)))
        self.assertTrue(AuditEvent.objects.filter(action='driver_agreement.create',target_id=str(da.pk)).exists());self.assertEqual(warnings,[])

    def test_validation(self):
        with self.assertRaisesMessage(services.DomainError,'driver'):self.make(driver=self.owner)
        with self.assertRaisesMessage(services.DomainError,'salary'):self.make(salary=0)
        with self.assertRaisesMessage(services.DomainError,'End date'):self.make(end=date(2026,8,1))
        other=Party.objects.create(organization=Organization.objects.create(name='Other'),kind='driver',name='X')
        with self.assertRaisesMessage(services.DomainError,'organization'):self.make(driver=other)

    def test_overlap_with_other_driver_work_warns_but_does_not_block(self):
        da,_=self.make(driver=self.driver)                                  # Imran is also the driver on the worked agreement
        self.assertTrue(any('AGR-' in w for w in services.check_driver_overlaps(da)))
        da2,w2=self.make(driver=self.staff);da3,w3=self.make(driver=self.staff,start=date(2026,10,1));self.assertTrue(any('DRV-' in w for w in w3))

    # ---- periods and statements
    def test_issue_period_books_only_a_driver_obligation(self):
        da,_=self.make();p=self.issue(da)
        self.assertEqual((p.agreement_id,p.driver_agreement_id),(None,da.pk))
        self.assertEqual(list(p.obligations.values_list('kind','amount')),[('driver_salary',D(12000))])
        s=services.statement(p)
        self.assertEqual((s['driver']['earned'],s['driver']['due'],s['customer']['due'],s['owner']['due']),(D(12000),D(12000),D(0),D(0)))
        self.assertEqual(s['operator']['accrual_margin'],D(-12000));self.assertEqual(s['driver']['bill_state'],'outstanding')
        self.reconciles(p);self.assertEqual(services.period_parties(p),{'customer':None,'owner':None,'driver':self.staff})
        self.assertEqual(self.issue(da).pk,p.pk)    # issuing twice returns the same month

    def test_month_must_be_inside_the_agreement_dates(self):
        da,_=self.make(start=date(2026,9,1),end=date(2026,10,31))
        with self.assertRaisesMessage(services.DomainError,'not effective'):self.issue(da,date(2026,8,1))
        with self.assertRaisesMessage(services.DomainError,'not effective'):self.issue(da,date(2026,11,1))
        with self.assertRaisesMessage(services.DomainError,'first day'):self.issue(da,date(2026,9,5))

    def test_payments_advances_and_bill_states(self):
        da,_=self.make();p=self.issue(da)
        self.pay_driver(p,2000,'ADV-1',kind='salary_advance');self.pay_driver(p,10000,'PAY-1')
        s=services.statement(p);self.assertEqual((s['driver']['due'],s['driver']['advance'],s['driver']['paid'],s['driver']['bill_state']),(D(0),D(2000),D(12000),'paid'))
        self.reconciles(p)
        with self.assertRaisesMessage(services.DomainError,'exceeds'):self.pay_driver(p,1,'PAY-2')

    def test_absence_and_fine_reduce_the_salary_and_other_sides_do_not_exist(self):
        da,_=self.make();p=self.issue(da)
        services.post_charge(self.org,self.user,{'period_id':p.pk,'side':'driver','kind':'absence_deduction','quantity':D(2),'unit_rate':D(400),'description':'two days off'},'da-abs')
        services.post_charge(self.org,self.user,{'period_id':p.pk,'side':'driver','kind':'fine','amount':D(300),'description':'traffic fine'},'da-fine')
        self.assertEqual(services.statement(p)['driver']['due'],D(12000)-D(800)-D(300));self.reconciles(p)
        for side in('customer','owner'):
            with self.assertRaisesMessage(services.DomainError,f'no {side}'):services.post_charge(self.org,self.user,{'period_id':p.pk,'side':side,'kind':'charge','amount':D(10),'description':'x'},f'da-{side}')
        self.assertEqual(services.charge_suggestion(p,'absence_deduction',D(1))['unit_rate'],(D(12000)/30).quantize(D('0.01')))

    def test_vehicle_only_features_refuse_a_driver_period(self):
        da,_=self.make();p=self.issue(da)
        with self.assertRaisesMessage(services.DomainError,'vehicle agreement'):
            services.record_expense(self.org,self.user,{'period_id':p.pk,'category':'fuel','amount':D(100),'paid_by':'operator','responsible':'operator','offset_owner':False,'reference':'E1','event_date':date(2026,9,5)},'e1')
        with self.assertRaisesMessage(services.DomainError,'vehicle agreement'):services.issue_invoice(self.org,self.user,{'period_id':p.pk},'inv-1')
        with self.assertRaisesMessage(services.DomainError,'vehicle agreement'):services.prepare_settlement(self.org,self.user,p.pk)
        with self.assertRaisesMessage(services.DomainError,'vehicle agreement'):services.close_period(self.org,self.user,p.pk)

    # ---- the vehicle books are untouched
    def test_worked_example_and_vehicle_reports_do_not_move(self):
        before={r:self.rep(r)['rows'] for r in('customer-balances','owner-liability','income')}
        da,_=self.make(driver=self.driver);p=self.issue(da)
        s=services.statement(self.period);self.assertEqual((s['customer']['due'],s['owner']['due'],s['driver']['due'],s['operator']['accrual_margin']),(D(30000),D(12000),D(5000),D(20000)))
        for r,rows in before.items():self.assertEqual(self.rep(r)['rows'],rows,r)
        snap=self.c.get(f'{self.base}/snapshot?month=2026-09-01').json();self.assertEqual(len(snap['agreements']),1)

    def test_driver_reports_include_the_standalone_agreement(self):
        da,_=self.make();p=self.issue(da);self.pay_driver(p,4000,'PAY-R')
        r=self.rep('driver-payroll');self.assertEqual(r['count'],2)
        row=[x for x in r['rows'] if x[0]==f'DRV-{da.pk}'][0];self.assertEqual((row[2],row[-1]),('Solo Driver','8000.00'))
        self.assertEqual(r['bill_states'].count('outstanding'),2)
        cash=self.rep('cash-movement');self.assertEqual(cash['count'],2)
        self.assertEqual(self.rep('driver-payroll',f'?driver_id={self.staff.pk}')['count'],1)
        e=self.c.get(f'{self.base}/reports/driver-payroll/export');self.assertEqual(e.status_code,200);self.assertIn(f'DRV-{da.pk}',e.content.decode('utf-8-sig'))
        ent=self.rep('entries');self.assertTrue(any(x[1]==f'DRV-{da.pk}' for x in ent['rows']))

    def test_one_driver_two_balances_with_a_visible_split(self):
        da,_=self.make(driver=self.driver);self.issue(da)
        split=services.driver_balance_split(self.org,self.driver.pk)
        self.assertEqual((split['vehicle_agreements']['due'],split['standalone']['due'],split['total_due']),(D(5000),D(12000),D(17000)))
        self.assertEqual(split['standalone']['items'][0]['ref'],f'DRV-{da.pk}')

    # ---- revisions and ending
    def test_revision_applies_from_the_month_and_never_rewrites_history(self):
        da,_=self.make();p1=self.issue(da)
        services.revise_driver_agreement(self.org,self.user,da.pk,date(2026,10,1),{'salary':D(15000)},'raise')
        self.assertEqual(self.issue(da,date(2026,10,1)).obligations.get().amount,D(15000));self.assertEqual(p1.obligations.get().amount,D(12000))
        with self.assertRaisesMessage(services.DomainError,'already issued'):services.revise_driver_agreement(self.org,self.user,da.pk,date(2026,10,1),{'salary':D(16000)},'again')
        with self.assertRaisesMessage(services.DomainError,'reason'):services.revise_driver_agreement(self.org,self.user,da.pk,date(2026,12,1),{'salary':D(1)},' ')

    def test_ending_the_agreement_stops_new_months(self):
        da,_=self.make();self.issue(da)
        services.update_driver_agreement(self.org,self.user,da.pk,{'end_date':date(2026,9,30)})
        with self.assertRaisesMessage(services.DomainError,'not effective'):self.issue(da,date(2026,10,1))
        with self.assertRaisesMessage(services.DomainError,'before'):services.update_driver_agreement(self.org,self.user,da.pk,{'end_date':date(2026,8,1)})

    # ---- API and branches
    def test_api_round_trip(self):
        r=self.c.post(f'{self.base}/driver-agreements',data={'driver_id':self.staff.pk,'start_date':'2026-09-01','salary':'12000','terms':'Weekday duty, Friday off'},content_type='application/json')
        self.assertEqual(r.status_code,200,r.content);j=r.json();self.assertEqual((j['driver']['name'],j['salary'],j['terms']),('Solo Driver','12000.00','Weekday duty, Friday off'))
        i=j['id'];p=self.c.post(f'{self.base}/driver-agreements/{i}/periods?month=2026-09-01');self.assertEqual(p.status_code,200,p.content)
        d=self.c.get(f'{self.base}/driver-agreements/{i}').json();self.assertEqual((len(d['versions']),len(d['periods']),d['periods'][0]['due']),(1,1,'12000.00'))
        rv=self.c.post(f'{self.base}/driver-agreements/{i}/revise',data={'effective_from':'2026-10-01','salary':'13000','reason':'raise'},content_type='application/json');self.assertEqual(rv.status_code,200,rv.content)
        pt=self.c.patch(f'{self.base}/driver-agreements/{i}',data={'terms':'Updated','end_date':'2027-08-31'},content_type='application/json');self.assertEqual(pt.status_code,200,pt.content);self.assertEqual(pt.json()['end_date'],'2027-08-31')
        lst=self.c.get(f'{self.base}/driver-agreements').json();self.assertEqual(lst['count'],1)
        self.assertEqual(self.c.get(f'{self.base}/driver-agreements?driver_id={self.staff.pk}').json()['count'],1)
        self.assertEqual(self.c.get(f'{self.base}/driver-agreements?driver_id={self.driver.pk}').json()['count'],0)
        self.assertEqual(self.c.post(f'{self.base}/driver-agreements',data={'driver_id':self.owner.pk,'start_date':'2026-09-01','salary':'1'},content_type='application/json').status_code,422)

    def test_viewer_may_read_but_not_write(self):
        da,_=self.make()
        u=get_user_model().objects.get(username='viewer');c=Client();c.force_login(u)
        self.assertEqual(c.get(f'{self.base}/driver-agreements/{da.pk}').status_code,200)
        self.assertEqual(c.post(f'{self.base}/driver-agreements/{da.pk}/periods?month=2026-09-01').status_code,403)

    def test_a_branch_limited_member_cannot_see_another_branchs_driver_agreement(self):
        sylhet=Branch.objects.create(organization=self.org,name='Sylhet');main=services.default_branch(self.org)
        da,_=self.make(branch=sylhet);p=self.issue(da)
        u=get_user_model().objects.create_user('main-only',password='x');Membership.objects.create(organization=self.org,user=u,role='accountant',branch=main)
        c=Client();c.force_login(u)
        self.assertEqual(c.get(f'{self.base}/driver-agreements').json()['count'],0);self.assertEqual(c.get(f'{self.base}/driver-agreements/{da.pk}').status_code,404)
        self.assertEqual(c.post(f'{self.base}/driver-agreements/{da.pk}/periods?month=2026-10-01').status_code,404)
        self.assertEqual(c.get(f'{self.base}/reports/driver-payroll').json()['count'],1)         # only the Main-branch worked agreement
        self.assertEqual(self.admin_count(),2)
        self.assertEqual(c.get(f'{self.base}/periods').json()['count'],1)

    def admin_count(self):return self.rep('driver-payroll')['count']
