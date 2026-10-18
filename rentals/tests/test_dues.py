"""M12 S2: payment schedule per side and the dues derived from it (computed on demand, never stored)."""
from datetime import date
from decimal import Decimal
from django.test import Client
from ..models import Agreement, Party, Vehicle
from .. import services
from .base import RentalsTestCase

D=Decimal
MONTHLY5={'customer':{'kind':'monthly','day':5}}

class DuesBase(RentalsTestCase):
    def make(self,start=date(2026,9,1),end=date(2026,11,30),schedule=None,rent=30000,owner=12000,salary=6000,**extra):
        n=Vehicle.objects.count()
        o=Party.objects.create(organization=self.org,kind='owner',name=f'O{n}');cu=Party.objects.create(organization=self.org,kind='customer',name=f'C{n}');d=Party.objects.create(organization=self.org,kind='driver',name=f'D{n}')
        car=Vehicle.objects.create(organization=self.org,registration=f'DU-{n}',model_name='M',owner=o)
        a=Agreement(organization=self.org,vehicle=car,customer=cu,driver=d,start_date=start,end_date=end,customer_rent=D(rent),owner_rent=D(owner),driver_salary=D(salary),payment_schedule=schedule or {},**extra)
        return services.create_agreement(self.org,self.user,a)[0]

    def dues(self,a,side='customer',today=date(2026,9,2),until=None):return services.dues_for(a,side,today=today,until=until)
    def pay(self,period,kind,amount,ref,side='customer_rent'):
        ob=period.obligations.get(kind=side)
        return services.record_payment(self.org,self.user,{'kind':kind,'amount':D(amount),'reference':ref,'method':'bank','event_date':date(2026,9,20),'allocations':[{'obligation_id':ob.pk,'amount':D(amount)}]},ref)

class ScheduleDuesTests(DuesBase):
    def test_default_is_monthly_on_the_last_day_and_adds_up(self):
        a=self.make();d=self.dues(a)
        self.assertEqual([(x['due_date'],x['amount']) for x in d],[(date(2026,9,30),D('30000.00')),(date(2026,10,31),D('30000.00')),(date(2026,11,30),D('30000.00'))])
        self.assertEqual((d[0]['covers_from'],d[0]['covers_to'],d[1]['covers_from']),(date(2026,9,1),date(2026,9,30),date(2026,10,1)))

    def test_monthly_on_a_chosen_day_with_a_final_due_on_the_end_date(self):
        a=self.make(schedule=MONTHLY5);d=self.dues(a)
        self.assertEqual([x['due_date'] for x in d],[date(2026,9,5),date(2026,10,5),date(2026,11,5),date(2026,11,30)])
        self.assertEqual([x['amount'] for x in d],[D('5000.00'),D('29838.71'),D('30161.29'),D('25000.00')])
        self.assertEqual(sum(x['amount'] for x in d),D('90000.00'))                       # exactly the agreement total
        self.assertEqual((d[0]['covers_from'],d[1]['covers_from'],d[3]['covers_to']),(date(2026,9,1),date(2026,9,6),date(2026,11,30)))

    def test_a_day_after_the_month_length_means_the_last_day(self):
        a=self.make(schedule={'customer':{'kind':'monthly','day':31}},end=date(2026,10,31))
        self.assertEqual([x['due_date'] for x in self.dues(a)],[date(2026,9,30),date(2026,10,31)])

    def test_weekly_on_a_weekday_covers_every_day_once(self):
        a=self.make(schedule={'customer':{'kind':'weekly','weekday':4}});d=self.dues(a)      # Fridays
        self.assertTrue(all(x['due_date'].weekday()==4 for x in d[:-1]));self.assertEqual(d[0]['due_date'],date(2026,9,4))
        self.assertEqual(sum(x['amount'] for x in d),D('90000.00'));self.assertEqual((d[0]['covers_from'],d[-1]['covers_to']),(date(2026,9,1),date(2026,11,30)))
        for prev,nxt in zip(d,d[1:]):self.assertEqual((nxt['covers_from']-prev['covers_to']).days,1)       # no gaps, no overlaps

    def test_chosen_dates(self):
        a=self.make(schedule={'customer':{'kind':'dates','dates':['2026-10-15','2026-09-15']}});d=self.dues(a)
        self.assertEqual([x['due_date'] for x in d],[date(2026,9,15),date(2026,10,15),date(2026,11,30)])      # sorted, with the end of the agreement closing the last stretch
        self.assertEqual(sum(x['amount'] for x in d),D('90000.00'))

    def test_every_side_has_its_own_schedule(self):
        a=self.make(schedule={'owner':{'kind':'weekly','weekday':0},'driver':{'kind':'monthly','day':'last'}})
        self.assertEqual(self.dues(a,'customer')[0]['due_date'],date(2026,9,30))
        self.assertEqual(self.dues(a,'owner')[0]['due_date'],date(2026,9,7))
        self.assertEqual(sum(x['amount'] for x in self.dues(a,'owner')),D('36000.00'));self.assertEqual(sum(x['amount'] for x in self.dues(a,'driver')),D('18000.00'))

    def test_dues_follow_the_basis_and_a_part_month(self):
        a=self.make(start=date(2026,9,16),end=date(2026,10,31),customer_rent_basis='per_day',rent=1000)
        d=self.dues(a);self.assertEqual([x['amount'] for x in d],[D('15000.00'),D('31000.00')])

    def test_open_ended_agreements_list_dues_up_to_the_horizon(self):
        a=self.make(end=None);d=self.dues(a,today=date(2026,9,2),until=date(2026,12,31))
        self.assertEqual([x['due_date'] for x in d],[date(2026,9,30),date(2026,10,31),date(2026,11,30),date(2026,12,31)])

    def test_a_revision_changes_the_schedule_from_its_date(self):
        a=self.make()
        services.revise_agreement(self.org,self.user,a.pk,date(2026,11,1),{'payment_schedule':{'customer':{'kind':'monthly','day':10}}},'Pay on the 10th')
        d=self.dues(a);self.assertEqual([x['due_date'] for x in d],[date(2026,9,30),date(2026,10,31),date(2026,11,10),date(2026,11,30)])
        self.assertEqual(sum(x['amount'] for x in d),D('90000.00'))

    def test_bad_schedules_are_refused(self):
        for bad in({'customer':{'kind':'weekly','weekday':9}},{'customer':{'kind':'monthly','day':0}},{'customer':{'kind':'monthly','day':'first'}},{'customer':{'kind':'dates','dates':[]}},{'customer':{'kind':'dates','dates':['soon']}},{'customer':{'kind':'yearly'}},{'tenant':{'kind':'monthly','day':1}}):
            with self.assertRaises(services.DomainError,msg=str(bad)):self.make(schedule=bad)

class PaidStateTests(DuesBase):
    def test_payments_are_applied_oldest_first(self):
        a=self.make();sep=services.issue_period(self.org,a.pk,date(2026,9,1));octo=services.issue_period(self.org,a.pk,date(2026,10,1))
        self.pay(sep,'customer_payment',30000,'P1');self.pay(octo,'customer_payment',10000,'P2')
        d=self.dues(a,today=date(2026,11,10))
        self.assertEqual([(x['state'],x['paid']) for x in d],[('paid',D('30000.00')),('overdue',D('10000.00')),('due',D('0.00'))])
        self.assertEqual(d[1]['state'],'overdue');self.assertEqual(services.next_due(d)['due_date'],date(2026,10,31))        # the oldest unpaid due
        self.assertEqual(self.dues(a,today=date(2026,10,20))[1]['state'],'partly_paid')                                        # partly paid, not yet overdue

    def test_a_payment_counts_toward_the_month_it_was_billed_in(self):
        a=self.make();octo=services.issue_period(self.org,a.pk,date(2026,10,1))
        self.pay(octo,'customer_payment',20000,'P1')                                                                           # only October is billed and paid
        d=self.dues(a,today=date(2026,11,10))
        self.assertEqual([x['paid'] for x in d],[D('0.00'),D('20000.00'),D('0.00')])                                           # September stays unpaid, not first in line

    def test_paying_one_month_leaves_the_rest_due(self):
        a=self.make();sep=services.issue_period(self.org,a.pk,date(2026,9,1))
        self.pay(sep,'customer_payment',30000,'P1')
        self.assertEqual([x['state'] for x in self.dues(a,today=date(2026,10,5))],['paid','due','due'])

    def test_driver_advances_count_as_paid(self):
        a=self.make();sep=services.issue_period(self.org,a.pk,date(2026,9,1))
        self.pay(sep,'salary_advance',2000,'ADV',side='driver_salary')
        d=self.dues(a,'driver',today=date(2026,9,15));self.assertEqual((d[0]['state'],d[0]['paid']),('partly_paid',D('2000.00')))

class IssueMonthsTests(DuesBase):
    def test_catch_up_bills_every_month_to_now_once(self):
        a=self.make(start=date(2026,1,1),end=None)
        issued=services.issue_months_through(self.org,a.pk,date(2026,4,18))
        self.assertEqual([p.month for p in issued],[date(2026,1,1),date(2026,2,1),date(2026,3,1),date(2026,4,1)])
        self.assertEqual(services.issue_months_through(self.org,a.pk,date(2026,5,2))[0].month,date(2026,5,1))                  # only the new month is added
        self.assertEqual(services.issue_months_through(self.org,a.pk,date(2026,5,2)),[])                                       # nothing twice

    def test_catch_up_stops_at_the_agreement_end(self):
        a=self.make(start=date(2026,1,1),end=date(2026,2,28))
        self.assertEqual(len(services.issue_months_through(self.org,a.pk,date(2026,10,1))),2)

    def test_an_old_month_can_be_billed_on_its_own_and_part_paid(self):
        a=self.make(start=date(2026,1,1),end=None)
        jan=services.issue_period(self.org,a.pk,date(2026,1,1));self.pay(jan,'customer_payment',20000,'JAN-1')
        d=self.dues(a,today=date(2026,3,5));self.assertEqual((d[0]['state'],d[0]['paid']),('overdue',D('20000.00')))

class DuesApiTests(DuesBase):
    def setUp(self):
        super().setUp();self.c=Client();self.c.force_login(self.user);self.base=f'/api/v1/orgs/{self.org.pk}'

    def test_endpoint_lists_dues_and_the_next_one(self):
        a=self.make(schedule=MONTHLY5)
        r=self.c.get(f'{self.base}/agreements/{a.pk}/dues?side=customer&until=2026-12-31');self.assertEqual(r.status_code,200,r.content);j=r.json()
        self.assertEqual((j['side'],j['schedule']['kind'],len(j['items'])),('customer','monthly',4));self.assertEqual(j['items'][0]['amount'],'5000.00');self.assertIn('next_due',j)
        self.assertEqual(self.c.get(f'{self.base}/agreements/{a.pk}/dues?side=tenant').status_code,422)

    def test_create_and_revise_take_a_schedule_and_detail_returns_it(self):
        o=Party.objects.create(organization=self.org,kind='owner',name='AO');cu=Party.objects.create(organization=self.org,kind='customer',name='AC');d=Party.objects.create(organization=self.org,kind='driver',name='AD')
        car=Vehicle.objects.create(organization=self.org,registration='API-DU',model_name='M',owner=o)
        body={'vehicle_id':car.pk,'customer_id':cu.pk,'driver_id':d.pk,'start_date':'2026-09-01','customer_rent':'30000','owner_rent':'12000','driver_salary':'6000','payment_schedule':{'customer':{'kind':'weekly','weekday':4}}}
        r=self.c.post(f'{self.base}/agreements',data=body,content_type='application/json');self.assertEqual(r.status_code,200,r.content);i=r.json()['id']
        self.assertEqual(r.json()['payment_schedule']['customer']['weekday'],4)
        bad=dict(body,registration='x',payment_schedule={'customer':{'kind':'weekly','weekday':9}});bad['vehicle_id']=car.pk
        self.assertEqual(self.c.post(f'{self.base}/agreements',data=bad,content_type='application/json').status_code,422)
        rv=self.c.post(f'{self.base}/agreements/{i}/revise',data={'effective_from':'2026-11-01','reason':'monthly','changes':{'payment_schedule':{'customer':{'kind':'monthly','day':'last'}}}},content_type='application/json');self.assertEqual(rv.status_code,200,rv.content)
        det=self.c.get(f'{self.base}/agreements/{i}').json();self.assertEqual(det['versions'][-1]['payment_schedule']['customer']['kind'],'monthly');self.assertIn('next_dues',det)

class AgingTests(DuesBase):
    def aging_days(self,a,as_of):
        c=Client();c.force_login(self.user)
        rows=c.get(f'/api/v1/orgs/{self.org.pk}/reports/customer-aging?agreement_id={a.pk}&as_of={as_of}').json()['rows'];return rows[0][-1]

    def test_days_overdue_follow_the_oldest_unpaid_due_date_and_default_agreements_do_not_move(self):
        default=self.make();custom=self.make(schedule=MONTHLY5)
        for a in(default,custom):services.issue_period(self.org,a.pk,date(2026,9,1))
        self.assertEqual(self.aging_days(default,'2026-10-20'),20)          # month end (30 Sep) as before
        self.assertEqual(self.aging_days(custom,'2026-10-20'),45)           # first due on 5 Sep, unpaid

class PreviewTests(DuesBase):
    def test_preview_saves_nothing_and_shows_days_months_and_first_dues(self):
        c=Client();c.force_login(self.user);url=f'/api/v1/orgs/{self.org.pk}/agreement-preview'
        before=Agreement.objects.count()
        r=c.post(url,data={'start':'2026-09-16','end':'2026-11-30','rent':'1000','owner_rent':'12000','salary':'6000','rent_basis':'per_day','payment_schedule':{'customer':{'kind':'weekly','weekday':4}}},content_type='application/json')
        self.assertEqual(r.status_code,200,r.content);j=r.json()
        self.assertEqual((j['days'],j['months'][0]['month'],j['months'][0]['customer'],j['months'][1]['customer']),(76,'2026-09-01','15000.00','31000.00'))
        self.assertEqual(j['totals']['customer'],'76000.00');self.assertEqual(j['dues']['customer'][0]['due_date'],'2026-09-18');self.assertEqual(len(j['dues']['customer']),4)
        self.assertEqual(Agreement.objects.count(),before)
        no_driver=c.post(url,data={'start':'2026-09-01','end':'2026-09-30','rent':'1000','owner_rent':'500','has_driver':False},content_type='application/json').json();self.assertNotIn('driver',no_driver['dues'])
        self.assertEqual(c.post(url,data={'start':'2026-09-01','rent':'1000','owner_rent':'500','rent_basis':'fixed_total'},content_type='application/json').status_code,422)
        self.assertEqual(c.post(url,data={'start':'2026-09-05','end':'2026-09-01','rent':'1','owner_rent':'1'},content_type='application/json').status_code,422)
        open_ended=c.post(url,data={'start':'2026-09-01','rent':'30000','owner_rent':'12000'},content_type='application/json').json();self.assertEqual((open_ended['days'],len(open_ended['months'])),(None,3))

class WorkspaceTests(DuesBase):
    def test_workspace_policies_are_live_and_read_only(self):
        c=Client();c.force_login(self.user);r=c.get(f'/api/v1/orgs/{self.org.pk}/workspace');self.assertEqual(r.status_code,200,r.content);j=r.json()
        self.assertEqual((j['name'],j['currency'],j['default_expense_threshold']),(self.org.name,'BDT','5000.00'));self.assertEqual(j['message_delivery'],'not_connected');self.assertIn('cutoff_day',j['month_end'])
        self.assertEqual(Client().get(f'/api/v1/orgs/{self.org.pk}/workspace').status_code,401)

class SnapshotQueryTests(DuesBase):
    def test_snapshot_query_count_does_not_grow_with_agreements(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        c=Client();c.force_login(self.user);url=f'/api/v1/orgs/{self.org.pk}/snapshot?month=2026-09-01'
        a=self.make();p=services.issue_period(self.org,a.pk,date(2026,9,1));self.pay(p,'customer_payment',1000,'SQ-1')
        c.get(url)                                                            # warm-up: the first request sets up the session
        with CaptureQueriesContext(connection) as few:r1=c.get(url)
        for i in range(4):
            b=self.make();q=services.issue_period(self.org,b.pk,date(2026,9,1));self.pay(q,'customer_payment',500+i,f'SQ-B{i}')
            services.record_expense(self.org,self.user,{'period_id':q.pk,'category':'fuel','amount':D(100),'paid_by':'operator','responsible':'owner','offset_owner':False,'reference':f'SQE{i}','event_date':date(2026,9,5)},f'sqe{i}')
        with CaptureQueriesContext(connection) as many:r2=c.get(url)
        self.assertEqual((r1.status_code,r2.status_code),(200,200));self.assertEqual(len(r2.json()['agreements']),6);self.assertGreaterEqual(len(r2.json()['entries']),9)
        print('FEW',[q['sql'][:90] for q in few.captured_queries]);print('MANY',[q['sql'][:90] for q in many.captured_queries])
        self.assertEqual(len(few),len(many))

class SeedDemoTests(DuesBase):
    def test_seed_demo_follows_the_requested_month_and_never_dates_money_in_the_future(self):
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from io import StringIO
        from ..models import Payment
        get_user_model().objects.create_user('seeder',password='x')
        call_command('seed_demo','--username','seeder','--month','2026-03',stdout=StringIO())
        pay=Payment.objects.filter(reference='BRAC-24590').first();self.assertEqual(pay.event_date,date(2026,3,20))
        s=services.statement(pay.allocations.first().obligation.period);self.assertEqual((s['customer']['due'],s['owner']['due'],s['driver']['due']),(D(30000),D(12000),D(5000)))
        self.assertEqual(s['month'],'2026-03-01')
