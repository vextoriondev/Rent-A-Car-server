"""M12 S1: date-based rent. Pure engine tests first, then issue_period, revisions and the API on top of them."""
from datetime import date
from decimal import Decimal
from django.test import SimpleTestCase
from ..models import Agreement, Party, Period, Vehicle
from .. import services
from ..services import rent_calc as rc
from .base import RentalsTestCase

D=Decimal

def segs(entries,start=date(2026,1,1),end=None):return rc.build_segments(entries,start,end)

class EngineTests(SimpleTestCase):
    def test_whole_month_bills_exactly_the_monthly_amount(self):
        s=segs([(date(2026,1,1),'per_month',D(30000))])
        for m in(date(2026,2,1),date(2026,3,1),date(2026,9,1)):self.assertEqual(rc.month_amount(s,m),D('30000.00'))

    def test_part_month_prorates_by_days_in_that_month(self):
        s=segs([(date(2026,2,10),'per_month',D(30000))],start=date(2026,2,10))
        self.assertEqual(rc.month_amount(s,date(2026,2,1)),D('20357.14'))            # 19 of 28 days
        s=segs([(date(2026,3,10),'per_month',D(30000))],start=date(2026,3,10))
        self.assertEqual(rc.month_amount(s,date(2026,3,1)),D('21290.32'))            # 22 of 31 days

    def test_end_date_stops_the_month_and_later_months_are_zero(self):
        s=segs([(date(2026,1,1),'per_month',D(31000))],end=date(2026,1,10))
        self.assertEqual(rc.month_amount(s,date(2026,1,1)),D('10000.00'));self.assertEqual(rc.month_amount(s,date(2026,2,1)),D('0.00'))

    def test_per_day_rate(self):
        s=segs([(date(2026,1,1),'per_day',D(1000))])
        self.assertEqual(rc.month_amount(s,date(2026,1,1)),D('31000.00'));self.assertEqual(rc.month_amount(s,date(2026,2,1)),D('28000.00'))
        s=segs([(date(2026,2,5),'per_day',D(1000))],start=date(2026,2,5),end=date(2026,2,14))
        self.assertEqual(rc.month_amount(s,date(2026,2,1)),D('10000.00'))

    def test_fixed_total_always_sums_to_the_total(self):
        s=segs([(date(2026,1,15),'fixed_total',D(100000))],start=date(2026,1,15),end=date(2026,3,14))
        parts=[rc.month_amount(s,date(2026,m,1)) for m in(1,2,3)]
        self.assertEqual(parts,[D('28813.64'),D('47457.76'),D('23728.60')]);self.assertEqual(sum(parts),D('100000.00'))
        s=segs([(date(2026,1,1),'fixed_total',D(100000))],end=date(2026,12,31))      # 365 days: remainder lands on the last day's month
        self.assertEqual(sum(rc.month_amount(s,date(2026,m,1)) for m in range(1,13)),D('100000.00'))

    def test_fixed_total_needs_an_end(self):
        with self.assertRaises(services.DomainError):segs([(date(2026,1,1),'fixed_total',D(100000))])

    def test_a_revision_in_the_middle_of_a_month_is_computed_in_segments(self):
        s=segs([(date(2026,1,1),'per_month',D(30000)),(date(2026,1,16),'per_month',D(33000))])
        self.assertEqual(rc.month_amount(s,date(2026,1,1)),D('31548.39'))            # 15/31 x 30000 + 16/31 x 33000
        self.assertEqual(rc.month_amount(s,date(2026,2,1)),D('33000.00'))

    def test_a_revision_on_the_first_changes_the_whole_month(self):
        s=segs([(date(2026,1,1),'per_month',D(30000)),(date(2026,2,1),'per_day',D(1100))])
        self.assertEqual(rc.month_amount(s,date(2026,1,1)),D('30000.00'));self.assertEqual(rc.month_amount(s,date(2026,2,1)),D('30800.00'))

    def test_a_basis_change_inside_a_fixed_total_segment_uses_the_segments_own_range(self):
        s=segs([(date(2026,1,1),'fixed_total',D(31000)),(date(2026,2,1),'per_month',D(30000))])   # v1 runs only until 31 Jan
        self.assertEqual(rc.month_amount(s,date(2026,1,1)),D('31000.00'));self.assertEqual(rc.month_amount(s,date(2026,2,1)),D('30000.00'))

class IssuePeriodTests(RentalsTestCase):
    def make(self,start,end=None,rent=30000,owner=12000,salary=5000,**extra):
        o=Party.objects.create(organization=self.org,kind='owner',name=f'O{Vehicle.objects.count()}');cu=Party.objects.create(organization=self.org,kind='customer',name=f'C{Vehicle.objects.count()}')
        d=Party.objects.create(organization=self.org,kind='driver',name=f'D{Vehicle.objects.count()}')
        car=Vehicle.objects.create(organization=self.org,registration=f'RC-{Vehicle.objects.count()}',model_name='M',owner=o)
        a=Agreement(organization=self.org,vehicle=car,customer=cu,driver=d,start_date=start,end_date=end,customer_rent=D(rent),owner_rent=D(owner),driver_salary=D(salary),**extra)
        return services.create_agreement(self.org,self.user,a)[0]

    def amounts(self,p):return {o.kind:o.amount for o in p.obligations.all()}

    def reconciles(self,p):
        self.assertEqual(services.diff_statements(services.ledger_statement(p),services.obligation_statement(p)),[])

    def test_the_worked_example_months_are_unchanged(self):
        self.assertEqual(self.amounts(self.period),{'customer_rent':D('100000.00'),'owner_rent':D('60000.00'),'driver_salary':D('20000.00')})   # a full month is exact

    def test_a_mid_month_start_bills_only_the_days_it_runs(self):
        a=self.make(date(2026,9,23));p=services.issue_period(self.org,a.pk,date(2026,9,1))
        self.assertEqual(self.amounts(p),{'customer_rent':D('8000.00'),'owner_rent':D('3200.00'),'driver_salary':D('1333.33')})   # 8 of 30 days
        self.reconciles(p)
        self.assertEqual(self.amounts(services.issue_period(self.org,a.pk,date(2026,10,1)))['customer_rent'],D('30000.00'))

    def test_an_end_date_prorates_the_last_month_and_refuses_the_next(self):
        a=self.make(date(2026,9,1),end=date(2026,10,10))
        self.assertEqual(self.amounts(services.issue_period(self.org,a.pk,date(2026,10,1)))['customer_rent'],D('9677.42'))          # 10 of 31 days
        with self.assertErrorCode('agreement_is_not_effective_this_month'):services.issue_period(self.org,a.pk,date(2026,11,1))

    def test_each_side_can_have_its_own_basis(self):
        a=self.make(date(2026,2,1),end=date(2026,4,30),rent=1000,owner=60000,salary=18000,customer_rent_basis='per_day',owner_rent_basis='fixed_total',driver_salary_basis='per_month')
        feb=self.amounts(services.issue_period(self.org,a.pk,date(2026,2,1)))
        self.assertEqual((feb['customer_rent'],feb['driver_salary']),(D('28000.00'),D('18000.00')))     # 28 days x 1,000; a full month of salary
        total=sum(self.amounts(services.issue_period(self.org,a.pk,date(2026,m,1)))['owner_rent'] for m in(2,3,4))
        self.assertEqual(total,D('60000.00'))                                                            # the fixed total is paid out exactly
        self.assertEqual(self.amounts(Period.objects.get(agreement=a,month=date(2026,3,1)))['customer_rent'],D('31000.00'))

    def test_fixed_total_needs_an_end_date(self):
        with self.assertErrorCode('a_fixed_total_needs_an_end_date'):self.make(date(2026,2,1),customer_rent_basis='fixed_total')

    def test_unknown_basis_is_refused(self):
        with self.assertErrorCode('unknown_rent_basis_basis_use_per_day'):self.make(date(2026,2,1),owner_rent_basis='per_week')

    def test_a_revision_mid_month_is_computed_in_segments(self):
        services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,15),{'driver_salary':'25000'},'Raise')
        v=self.agreement.versions.get(number=2);self.assertEqual(v.effective_from,date(2026,10,15))               # no longer rounded up to the 1st
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        self.assertEqual(self.amounts(october)['driver_salary'],D('22741.94'))                                    # 14/31 x 20,000 + 17/31 x 25,000
        self.assertEqual(self.amounts(october)['customer_rent'],D('100000.00'))
        self.assertEqual(self.amounts(services.issue_period(self.org,self.agreement.pk,date(2026,11,1)))['driver_salary'],D('25000.00'))
        self.reconciles(october)

    def test_a_revision_cannot_reach_into_an_issued_month_and_history_is_untouched(self):
        before=services.statement(self.period)
        with self.assertErrorCode('periods_from_that_date_are_already_issued_2'):services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,9,20),{'owner_rent':'1'},'x')
        self.assertEqual(services.statement(Period.objects.get(pk=self.period.pk)),before)
        with self.assertErrorCode('revision_starts_before_the_agreement_starts'):services.revise_agreement(self.org,self.user,self.agreement.pk,date(2025,12,1),{'owner_rent':'1'},'x')

    def test_revising_the_basis_applies_from_its_date(self):
        services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'customer_rent':'3500','customer_rent_basis':'per_day'},'Daily rate')
        self.assertEqual(self.amounts(services.issue_period(self.org,self.agreement.pk,date(2026,10,1)))['customer_rent'],D('108500.00'))   # 31 x 3,500
        with self.assertErrorCode('a_fixed_total_needs_an_end_date'):services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,12,1),{'owner_rent_basis':'fixed_total'},'x')

    def test_extending_and_ending_early_by_a_dated_revision_of_the_end_date(self):
        a=self.make(date(2026,9,1),end=date(2026,9,30))
        services.update_agreement_dates(self.org,self.user,a.pk,{'end_date':date(2026,11,15)})
        self.assertEqual(self.amounts(services.issue_period(self.org,a.pk,date(2026,11,1)))['customer_rent'],D('15000.00'))     # 15 of 30 days
        with self.assertErrorCode('a_month_that_this_change_would_reprice'):services.update_agreement_dates(self.org,self.user,a.pk,{'end_date':date(2026,10,10)})
        b=self.make(date(2026,9,1),end=date(2026,9,30))
        services.update_agreement_dates(self.org,self.user,b.pk,{'end_date':None})                       # open-ended again: nothing was issued past the old end
        self.assertEqual(self.amounts(services.issue_period(self.org,b.pk,date(2026,12,1)))['customer_rent'],D('30000.00'))

    def test_api_accepts_dates_and_bases_and_returns_them(self):
        from django.test import Client
        c=Client();c.force_login(self.user);base=f'/api/v1/orgs/{self.org.pk}'
        o=Party.objects.create(organization=self.org,kind='owner',name='ApiO');cu=Party.objects.create(organization=self.org,kind='customer',name='ApiC');d=Party.objects.create(organization=self.org,kind='driver',name='ApiD')
        car=Vehicle.objects.create(organization=self.org,registration='API-RC',model_name='M',owner=o)
        body={'vehicle_id':car.pk,'customer_id':cu.pk,'driver_id':d.pk,'start_date':'2026-02-10','end_date':'2026-05-31','customer_rent':'1000','owner_rent':'60000','driver_salary':'18000','customer_rent_basis':'per_day','owner_rent_basis':'fixed_total','driver_salary_basis':'per_month'}
        r=c.post(f'{base}/agreements',data=body,content_type='application/json');self.assertEqual(r.status_code,200,r.content)
        j=r.json();self.assertEqual((j['customer_rent_basis'],j['owner_rent_basis'],j['driver_salary_basis'],j['end_date']),('per_day','fixed_total','per_month','2026-05-31'))
        rv=c.post(f"{base}/agreements/{j['id']}/revise",data={'effective_from':'2026-03-15','reason':'rate','changes':{'customer_rent':'1100'}},content_type='application/json');self.assertEqual(rv.status_code,200,rv.content)
        self.assertEqual(rv.json()['effective_from'],'2026-03-15')
        bad=dict(body,registration='x',vehicle_id=car.pk,owner_rent_basis='per_year');self.assertEqual(c.post(f'{base}/agreements',data=bad,content_type='application/json').status_code,422)
        d2=c.get(f"{base}/agreements/{j['id']}").json();self.assertEqual(d2['versions'][-1]['customer_rent_basis'],'per_day')

class QuickEndpointTests(RentalsTestCase):
    def test_quick_wizard_endpoint_takes_end_date_bases_and_schedule(self):
        from django.test import Client
        c=Client();c.force_login(self.user)
        body={'customer':'QC','owner':'QO','driver':'QD','vehicle':'Noah','registration':'QK-1','start':'2026-09-10','end':'2026-12-31','rent':'1000','owner_rent':'600','salary':'18000','rent_basis':'per_day','owner_rent_basis':'fixed_total','salary_basis':'per_month','payment_schedule':{'customer':{'kind':'weekly','weekday':4}}}
        r=c.post(f'/api/v1/orgs/{self.org.pk}/agreements/quick',data=body,content_type='application/json');self.assertEqual(r.status_code,200,r.content);j=r.json()
        self.assertEqual((j['end_date'],j['customer_rent_basis'],j['owner_rent_basis'],j['payment_schedule']['customer']['weekday']),('2026-12-31','per_day','fixed_total',4))
        sep=services.issue_period(self.org,j['id'],date(2026,9,1));self.assertEqual(sep.obligations.get(kind='customer_rent').amount,D('21000.00'))   # 21 days from 10 Sept x 1,000
        bad=dict(body,registration='QK-2',end=None);self.assertEqual(c.post(f'/api/v1/orgs/{self.org.pk}/agreements/quick',data=bad,content_type='application/json').status_code,422)   # fixed total needs an end date
