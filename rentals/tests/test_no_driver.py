"""M16a: agreements without a driver (vehicle only). The driver, if any, is on a separate driver agreement (M16b)."""
from datetime import date
from decimal import Decimal
from ..models import Agreement, Obligation, Party, Vehicle
from .. import services
from .test_reports import ReportBase

D=Decimal

class NoDriverTests(ReportBase):
    def vehicle_only(self,month=date(2026,10,1),**extra):
        o=Party.objects.create(organization=self.org,kind='owner',name='Solo Owner');cu=Party.objects.create(organization=self.org,kind='customer',name='Solo Customer')
        car=Vehicle.objects.create(organization=self.org,registration='SYL-1',model_name='Noah',owner=o)
        a=Agreement(organization=self.org,vehicle=car,customer=cu,driver=None,start_date=date(2026,1,1),customer_rent=D(50000),owner_rent=D(30000),driver_salary=D(0),driver_provider='none',**extra)
        a,_=services.create_agreement(self.org,self.user,a)
        return a,services.issue_period(self.org,a.pk,month)

    def test_no_driver_agreement_issues_only_customer_and_owner_obligations(self):
        a,p=self.vehicle_only()
        self.assertIsNone(a.driver_id);self.assertFalse(a.driver_assignments.exists())
        self.assertEqual(sorted(p.obligations.values_list('kind',flat=True)),['customer_rent','owner_rent'])
        s=services.statement(p)
        self.assertEqual((s['customer']['due'],s['owner']['due'],s['operator']['accrual_margin']),(D(50000),D(30000),D(20000)))
        self.assertEqual((s['driver']['earned'],s['driver']['due']),(D(0),D(0)))
        self.assertEqual(services.diff_statements(services.ledger_statement(p),services.obligation_statement(p)),[])
        self.assertEqual(services.period_parties(p)['driver'],None)

    def test_worked_example_is_unchanged(self):
        s=services.statement(self.period)
        self.assertEqual((s['customer']['due'],s['owner']['due'],s['driver']['due'],s['operator']['accrual_margin']),(D(30000),D(12000),D(5000),D(20000)))

    def test_a_driver_or_salary_is_refused_with_provider_none_and_a_driver_is_required_otherwise(self):
        o=self.owner;cu=self.customer;car=Vehicle.objects.create(organization=self.org,registration='X-1',model_name='M',owner=o)
        with self.assertErrorCode('a_vehicle_only_agreement_has_no_driver'):
            services.create_agreement(self.org,self.user,Agreement(organization=self.org,vehicle=car,customer=cu,driver=self.driver,start_date=date(2026,1,1),customer_rent=D(1),owner_rent=D(1),driver_salary=D(0),driver_provider='none'))
        with self.assertErrorCode('a_vehicle_only_agreement_has_no_driver'):
            services.create_agreement(self.org,self.user,Agreement(organization=self.org,vehicle=car,customer=cu,driver=None,start_date=date(2026,1,1),customer_rent=D(1),owner_rent=D(1),driver_salary=D(500),driver_provider='none'))
        with self.assertErrorCode('this_agreement_needs_a_driver_choose_no'):
            services.create_agreement(self.org,self.user,Agreement(organization=self.org,vehicle=car,customer=cu,driver=None,start_date=date(2026,1,1),customer_rent=D(1),owner_rent=D(1),driver_salary=D(500),driver_provider='operator'))

    def test_nothing_can_target_a_missing_driver(self):
        a,p=self.vehicle_only()
        with self.assertErrorCode('this_agreement_has_no_side'):
            services.post_charge(self.org,self.user,{'period_id':p.pk,'side':'driver','kind':'charge','amount':D(100),'description':'x'},'nd-1')
        for kw in({'responsible':'driver','paid_by':'operator'},{'responsible':'operator','paid_by':'driver'}):
            with self.assertErrorCode('this_agreement_has_no_driver_so_a'):self.expense('ND-E',100,date(2026,10,5),period=p,**kw)
        # the operator can still record an expense for the owner or itself
        self.expense('ND-E2',100,date(2026,10,5),period=p)   # default rule: owner responsible, operator paid

    def test_reports_skip_the_driver_and_still_show_the_agreement(self):
        a,p=self.vehicle_only(month=date(2026,9,1))
        self.assertEqual(self.rep('driver-payroll')['count'],1)                      # only the worked agreement has a driver
        self.assertEqual(self.rep('customer-balances')['count'],2)
        self.assertEqual(self.rep('income','?agreement_id=%d'%a.pk)['rows'][0][-1],'20000.00')

    def test_snapshot_and_agreement_detail_say_driver_is_null(self):
        a,p=self.vehicle_only()
        row=[x for x in self.c.get(f'{self.base}/snapshot?month=2026-10-01').json()['agreements'] if x['id']==f'AGR-{a.pk}'][0]
        self.assertIsNone(row['driver']);self.assertEqual(row['salary'],0)
        d=self.c.get(f'{self.base}/agreements/{a.pk}').json()
        self.assertIsNone(d['driver']);self.assertEqual(d['driver_provider'],'none')

    def test_api_creates_a_no_driver_agreement(self):
        o=Party.objects.create(organization=self.org,kind='owner',name='Api Owner');cu=Party.objects.create(organization=self.org,kind='customer',name='Api Cust')
        car=Vehicle.objects.create(organization=self.org,registration='API-1',model_name='M',owner=o)
        r=self.c.post(f'{self.base}/agreements',data={'vehicle_id':car.pk,'customer_id':cu.pk,'start_date':'2026-10-01','customer_rent':'40000','owner_rent':'25000','driver_salary':'0','driver_provider':'none'},content_type='application/json')
        self.assertEqual(r.status_code,200,r.content);self.assertIsNone(r.json()['driver'])

    def test_adding_a_driver_later_by_revision_and_assignment(self):
        a,p=self.vehicle_only()
        services.revise_agreement(self.org,self.user,a.pk,date(2026,11,1),{'driver_provider':'operator','driver_paid_by':'operator','driver_salary':D(15000)},'driver joins')
        with self.assertErrorCode('assign_a_driver_before_issuing_this_month'):services.issue_period(self.org,a.pk,date(2026,11,1))
        services.assign_driver(self.org,self.user,a.pk,self.driver.pk,date(2026,11,1))
        p2=services.issue_period(self.org,a.pk,date(2026,11,1))
        self.assertEqual(services.statement(p2)['driver']['earned'],D(15000))
        self.assertEqual(services.statement(p)['driver']['earned'],D(0))          # October stays vehicle-only

    def test_revising_to_no_driver_stops_the_salary_from_the_next_month(self):
        services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'driver_provider':'none','driver_salary':D(0)},'driver leaves')
        p=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        self.assertNotIn('driver_salary',list(p.obligations.values_list('kind',flat=True)))
        self.assertEqual(services.statement(self.period)['driver']['earned'],D(20000))   # September keeps its salary

    def test_overlap_warning_ignores_missing_drivers(self):
        a,p=self.vehicle_only()
        self.assertEqual(services.check_overlaps(a),[])

def expense_helper(self,ref,amount,day,category='fuel',responsible='owner',paid_by='operator',period=None):
    return services.record_expense(self.org,self.user,{'period_id':(period or self.period).pk,'category':category,'amount':D(amount),'paid_by':paid_by,'responsible':responsible,'offset_owner':False,'reference':ref,'event_date':day},ref)
NoDriverTests.expense=expense_helper
