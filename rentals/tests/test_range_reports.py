"""M13: any-period inspection, daily expenses, cost categories, income report, bill state."""
from datetime import date
from decimal import Decimal
from django.db import connection
from django.test.utils import CaptureQueriesContext
from ..models import AuditEvent, Membership
from .. import services
from .test_reports import ReportBase

D=Decimal

class M13Base(ReportBase):
    def expense(self,ref,amount,day,category='fuel',responsible='owner',paid_by='operator',period=None):
        return services.record_expense(self.org,self.user,{'period_id':(period or self.period).pk,'category':category,'amount':D(amount),'paid_by':paid_by,'responsible':responsible,'offset_owner':False,'reference':ref,'event_date':day},ref)

    def patch(self,path,body):
        return self.c.patch(f'{self.base}{path}',data=body,content_type='application/json')

class RangeTests(M13Base):
    def test_a_range_is_exact_and_the_month_filter_stays_a_whole_month(self):          # M13c replaced the "overlapping months" rule for from/to
        services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        by_month=self.rep('customer-balances','?month=2026-09-01')
        by_range=self.rep('customer-balances','?from=2026-09-01&to=2026-09-30')
        self.assertEqual(by_range['rows'][0][4:7],by_month['rows'][0][4:7])            # a whole month gives the same figures either way
        inside=self.rep('customer-balances','?from=2026-09-10&to=2026-09-20')['rows'][0]
        self.assertEqual(inside[4:7],['36666.67','70000.00','-3333.33'])               # 11 days of rent accrued; the payment of 20 Sep is in the window
        self.assertEqual(self.rep('customer-balances','?from=2026-09-30&to=2026-10-02')['count'],2)
        self.assertEqual(self.rep('customer-balances','?from=2026-11-01')['count'],2)  # both months still carry their balance into November
        self.assertEqual(self.rep('income','?to=2026-09-30')['count'],1)

    def test_bad_ranges_are_refused(self):
        self.assertEqual(self.c.get(f'{self.base}/reports/income?from=2026-10-02&to=2026-10-01').status_code,422)
        self.assertEqual(self.c.get(f'{self.base}/reports/income?from=2020-01-01&to=2026-10-01').status_code,422)
        for path in('/expenses','/payments','/periods'):
            self.assertEqual(self.c.get(f'{self.base}{path}?from=2026-10-02&to=2026-10-01').status_code,422,path)

    def test_entry_date_filters_on_expenses_payments_and_periods(self):
        self.expense('D1',100,date(2026,9,10));self.expense('D2',200,date(2026,9,25),category='toll')
        r=self.c.get(f'{self.base}/expenses?from=2026-09-20&to=2026-09-30').json();self.assertEqual(r['count'],1)
        r=self.c.get(f'{self.base}/expenses?category=fuel').json();self.assertEqual(r['count'],1);self.assertEqual(r['totals']['amount'],'100.00')
        allr=self.c.get(f'{self.base}/expenses').json();self.assertEqual(allr['totals']['count'],3)
        self.assertEqual(allr['totals']['by_state']['posted']['count'],3)
        self.assertEqual(self.c.get(f'{self.base}/expenses?vehicle_id={self.car.pk}').json()['count'],3)
        self.assertEqual(self.c.get(f'{self.base}/expenses?vehicle_id=999999').json()['count'],0)
        self.assertEqual(self.c.get(f'{self.base}/expenses?state=pending&responsible=owner').json()['count'],0)
        self.assertEqual(self.c.get(f'{self.base}/payments?from=2026-09-20&to=2026-09-20').json()['count'],3)
        self.assertEqual(self.c.get(f'{self.base}/payments?from=2026-10-01').json()['count'],0)
        self.assertEqual(self.c.get(f'{self.base}/periods?from=2026-08-15&to=2026-09-02').json()['count'],1)

    def test_snapshot_range_and_month_shape(self):
        services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        one=self.c.get(f'{self.base}/snapshot?month=2026-09-01').json();self.assertNotIn('periods',one['backend'])
        rng=self.c.get(f'{self.base}/snapshot?from=2026-09-01&to=2026-10-31').json()
        self.assertEqual(len(rng['backend']['periods'][f'AGR-{self.agreement.pk}']),2)
        self.assertEqual(len(rng['entries']),len(one['entries']))      # nothing was recorded in October
        self.assertEqual(self.c.get(f'{self.base}/snapshot').status_code,422)
        self.assertEqual(self.c.get(f'{self.base}/snapshot?from=2026-09-30&to=2026-09-01').status_code,422)

class DailyAndCategoryTests(M13Base):
    def test_daily_expenses_reconcile_to_posted_that_day(self):
        self.expense('X1',900,date(2026,9,3),category='fuel',responsible='owner');self.expense('X2',300,date(2026,9,3),category='toll',responsible='customer')
        self.expense('X3',700,date(2026,9,4))
        pend=self.expense('X4',6000,date(2026,9,3),category='cleaning')           # over the threshold: stays pending
        self.assertEqual(pend.state,'pending')
        r=self.rep('daily-expenses','?date=2026-09-03')
        self.assertEqual(r['column_ids'],['vehicle','agreement','category','amount','paid_by','responsible','status','reference','receipt'])
        self.assertEqual(r['count'],4)                                              # GAR-0182 repair (posted by the worked example) + three more
        posted=sum((D(x[3]) for x in r['rows'] if x[6]=='Recorded'),D(0))
        self.assertEqual(r['totals']['amount'],str(posted.quantize(D('0.01'))))
        self.assertEqual(sum(D(v) for v in r['breakdowns']['by_category'].values()),posted)
        self.assertEqual(sum(D(v) for v in r['breakdowns']['by_responsible'].values()),posted)
        self.assertEqual(self.rep('daily-expenses','?date=2026-09-03&state=pending')['count'],1)
        self.assertEqual(self.rep('daily-expenses','?date=2026-09-03&responsible=customer')['count'],1)
        self.assertEqual(self.rep('daily-expenses','?date=2026-09-03&category=toll')['rows'][0][8],'No')
        self.assertEqual(self.c.get(f'{self.base}/reports/daily-expenses').status_code,422)

    def test_daily_export_has_bom_preamble_and_breakdown(self):
        self.expense('X1',900,date(2026,9,3))
        r=self.c.get(f'{self.base}/reports/daily-expenses/export?date=2026-09-03');self.assertEqual(r.status_code,200)
        text=r.content.decode('utf-8');self.assertTrue(text.startswith('﻿'));self.assertIn('date=2026-09-03',text);self.assertIn('By category',text)
        self.assertTrue(AuditEvent.objects.filter(action='report.export',target_id='daily-expenses').exists())

    def test_category_by_vehicle_reconciles_to_cost_by_responsible(self):
        self.second_fleet()
        p2=self.org.period_set.exclude(pk=self.period.pk).get()
        self.expense('F1',900,date(2026,9,5));self.expense('F2',400,date(2026,9,6),period=p2);self.expense('F3',500,date(2026,9,7),category='toll')
        r=self.rep('category-by-vehicle','?category=fuel');self.assertEqual(r['count'],2)
        self.assertEqual({x[0]:x[3] for x in r['rows']},{'DHA-19-7724':'900.00','CTG-77':'400.00'})
        self.assertEqual(r['totals']['amount'],'1300.00')
        cost=sum((D(x[3]) for x in self.rep('cost-by-responsible')['rows'] if x[1]=='Fuel'),D(0))
        self.assertEqual(D(r['totals']['amount']),cost)
        self.assertEqual(self.rep('category-by-vehicle','?category=fuel&from=2026-09-06&to=2026-09-30')['totals']['amount'],'400.00')
        self.assertEqual(self.c.get(f'{self.base}/reports/category-by-vehicle').status_code,422)
        self.assertEqual(self.c.get(f'{self.base}/reports/category-by-vehicle?category=nope').status_code,422)

    def test_deactivated_category_refused_for_new_but_kept_in_history(self):
        self.expense('G1',900,date(2026,9,5))
        fuel=next(c for c in self.c.get(f'{self.base}/expense-categories').json() if c['code']=='fuel')
        self.assertEqual(self.patch(f'/expense-categories/{fuel["id"]}',{'active':False}).status_code,403)   # category.write is admin only
        Membership.objects.filter(user=self.user).update(role='admin')
        r=self.patch(f'/expense-categories/{fuel["id"]}',{'name':'Gas','active':False});self.assertEqual(r.status_code,200,r.content)
        self.assertEqual((r.json()['code'],r.json()['name'],r.json()['active']),('fuel','Gas',False))
        with self.assertErrorCode('The category "Gas" is deactivated and cannot be used for new expenses'):self.expense('G2',100,date(2026,9,6))
        self.assertEqual(self.rep('category-by-vehicle','?category=fuel')['totals']['amount'],'900.00')
        self.assertEqual(self.rep('daily-expenses','?date=2026-09-05&category=fuel')['count'],1)
        self.assertTrue(AuditEvent.objects.filter(action='category.update').exists())
        self.assertEqual(self.patch(f'/expense-categories/{fuel["id"]}',{'active':True}).status_code,200);self.expense('G3',100,date(2026,9,7))

class IncomeAgingBillStateTests(M13Base):
    def test_income_margin_equals_statement_margin(self):
        r=self.rep('income');self.assertEqual(r['column_ids'][4:],['billed','collected','money_to_take','owner_rent','driver_salary','your_costs','profit'])
        self.assertEqual(r['rows'][0][4:],['100000.00','70000.00','30000.00','60000.00','20000.00','0.00','20000.00'])   # worked example margin 20,000
        self.assertEqual(D(r['rows'][0][-1]),services.statement(self.period)['operator']['accrual_margin'])
        self.expense('I1',1500,date(2026,9,8),category='cleaning',responsible='operator')
        r=self.rep('income');self.assertEqual((r['rows'][0][-2],r['rows'][0][-1]),('1500.00','18500.00'))
        self.assertEqual(D(r['rows'][0][-1]),services.statement(self.period)['operator']['accrual_margin'])
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'customer','kind':'charge','amount':D(2500),'description':'late fee'},'chg-1')
        self.assertEqual(self.rep('income')['rows'][0][4],'102500.00')                  # billed = rent + charges - credits

    def test_aging_days_overdue(self):
        def days(as_of):return self.rep('customer-aging',f'?as_of={as_of}')['rows'][0][-1]
        self.assertEqual(self.rep('customer-aging')['column_ids'][-1],'days_overdue')
        self.assertEqual(days('2026-09-15'),0);self.assertEqual(days('2026-10-20'),20);self.assertEqual(days('2027-03-01'),152)
        self.assertNotIn('days_overdue',self.rep('customer-aging')['totals'])

    def test_bill_state_all_three_values_on_statements_and_reports(self):
        s=services.statement(self.period)
        self.assertEqual((s['customer']['bill_state'],s['owner']['bill_state'],s['driver']['bill_state']),('owed_to_us','outstanding','outstanding'))   # M13b: customer 30,000 unpaid = owed to us; owner 12,000 and driver 5,000 = outstanding
        self.assertEqual(self.rep('customer-balances')['bill_states'],['owed_to_us']);self.assertEqual(self.rep('owner-liability')['bill_states'],['outstanding']);self.assertEqual(self.rep('driver-payroll')['bill_states'],['outstanding'])
        self.pay('customer_payment','30000','R-C2');self.pay('owner_payout','12000','R-O2');self.pay('driver_payout','5000','R-D2')
        s=services.statement(self.period)
        self.assertEqual((s['customer']['bill_state'],s['owner']['bill_state'],s['driver']['bill_state']),('paid','paid','paid'))
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'owner','kind':'adjustment','amount':D(700),'direction':'decrease','description':'overpaid'},'adj-1')
        s=services.statement(self.period);self.assertEqual(s['owner']['bill_state'],'owed_to_us');self.assertTrue(s['owner']['owner_owes_operator'])
        self.assertEqual(self.rep('owner-liability')['bill_states'],['owed_to_us'])
        self.assertEqual(self.rep('customer-balances')['bill_states'],['paid']);self.assertEqual(self.rep('driver-payroll')['bill_states'],['paid'])
        self.assertNotIn('bill_states',self.rep('income'))
        self.assertEqual(self.c.get(f'{self.base}/periods/{self.period.pk}/statement').json()['owner']['bill_state'],'owed_to_us')
        self.assertEqual(services.diff_statements(services.ledger_statement(self.period),services.obligation_statement(self.period)),[])

    def test_invoice_bill_state(self):
        inv=self.c.post(f'{self.base}/invoices',data={'period_id':self.period.pk},content_type='application/json',HTTP_IDEMPOTENCY_KEY='inv-bs');self.assertEqual(inv.status_code,200,inv.content)
        self.assertEqual(inv.json()['bill_state'],'owed_to_us')
        self.assertEqual(self.c.get(f'{self.base}/invoices').json()['items'][0]['bill_state'],'owed_to_us')

    def test_query_count_is_constant_in_periods(self):
        services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        with CaptureQueriesContext(connection) as few:self.rep('income','?from=2026-09-01&to=2026-10-31')
        for m in(11,12):services.issue_period(self.org,self.agreement.pk,date(2026,m,1))
        with CaptureQueriesContext(connection) as many:self.rep('income','?from=2026-09-01&to=2026-12-31')
        self.assertEqual(len(few),len(many))
