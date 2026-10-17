"""M13c: exact date-range accounting with opening balance, movements and closing balance."""
from datetime import date, timedelta
from decimal import Decimal
from django.db import connection
from django.test import Client, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from ..models import Expense, JournalEntry, Payment
from .. import services
from ..services import ranged
from .test_reports import ReportBase

D=Decimal
SEP1,SEP30=date(2026,9,1),date(2026,9,30)

class EngineTests(SimpleTestCase):
    def test_unbounded_window_is_the_whole_amount_and_halves_split_exactly(self):
        a0,a1=SEP1,SEP30
        self.assertEqual(ranged.accrual_part(D(30000),a0,a1,None,None),D('30000.00'))
        self.assertEqual(ranged.accrual_part(D(30000),a0,a1,date(2026,9,1),date(2026,9,15)),D('15000.00'))
        self.assertEqual(ranged.accrual_part(D(30000),a0,a1,date(2026,9,16),date(2026,9,30)),D('15000.00'))

    def test_every_day_of_a_month_adds_back_to_the_month_exactly(self):
        for amount in(D('100000.00'),D('33333.35'),D('1.01'),D('8000.00')):
            total=sum(ranged.accrual_part(amount,SEP1,SEP30,d,d) for d in(SEP1+timedelta(days=i) for i in range(30)))
            self.assertEqual(total,amount,amount)

    def test_windows_outside_the_active_days_get_nothing_and_after_them_everything(self):
        a0,a1=date(2026,9,23),SEP30                                   # an agreement that runs 8 days of September
        self.assertEqual(ranged.accrual_part(D(8000),a0,a1,date(2026,9,1),date(2026,9,25)),D('3000.00'))
        self.assertEqual(ranged.accrual_part(D(8000),a0,a1,date(2026,9,1),date(2026,9,22)),D('0.00'))
        self.assertEqual(ranged.accrual_part(D(8000),a0,a1,date(2026,10,1),date(2026,10,31)),D('0.00'))
        self.assertEqual(ranged.accrual_part(D(8000),a0,a1,None,date(2026,10,31)),D('8000.00'))

class RangeBase(ReportBase):
    def st(self,lo,hi,agreement=None):return services.agreement_range_statement(agreement or self.agreement,lo,hi)
    def rows(self,rtype,qs):return self.rep(rtype,qs)

class EventDateTests(RangeBase):
    def test_every_writer_sets_an_event_date(self):
        self.assertEqual(JournalEntry.objects.filter(event_date__isnull=True).count(),0)
        pay=Payment.objects.get(reference='R-C');e=JournalEntry.objects.get(source_type='payment',source_id=str(pay.pk));self.assertEqual(e.event_date,pay.event_date)
        exp=Expense.objects.get(reference='GAR-0182');e=JournalEntry.objects.get(source_type='expense',source_id=str(exp.pk));self.assertEqual(e.event_date,exp.event_date)
        ob=self.period.obligations.get(kind='customer_rent');self.assertEqual(JournalEntry.objects.get(source_type='obligation',source_id=str(ob.pk)).event_date,SEP1)

    def test_a_charge_takes_its_posting_day_clamped_into_its_month(self):
        c=services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'customer','kind':'charge','amount':D(500),'description':'late fee'},'ev-1')
        e=JournalEntry.objects.get(source_type='charge',source_id=str(c.pk));self.assertTrue(SEP1<=e.event_date<=SEP30)        # posted "today" (October or later), counted in September

    def test_a_reversal_is_dated_when_it_is_made(self):
        exp=Expense.objects.get(reference='GAR-0182');services.reverse_expense(self.org,self.user,exp.pk,'wrong')
        e=JournalEntry.objects.get(source_type='expense_reversal',source_id=str(exp.pk));self.assertEqual(e.event_date,date.today())

    def test_backfill_restores_the_dates(self):
        before=dict(JournalEntry.objects.values_list('pk','event_date'))
        JournalEntry.objects.update(event_date=None)
        from ..services.event_dates import backfill_event_dates
        backfill_event_dates()
        after=dict(JournalEntry.objects.values_list('pk','event_date'))
        self.assertEqual({k:v for k,v in after.items() if v},{k:v for k,v in before.items() if v});self.assertNotIn(None,after.values())

class AgreementStatementTests(RangeBase):
    def test_a_range_inside_a_month_accrues_only_its_days(self):
        s=self.st(SEP1,date(2026,9,10))
        self.assertEqual((s['customer']['opening']['due'],s['customer']['movements']['billed'],s['customer']['movements']['received'],s['customer']['closing']['due']),(D('0.00'),D('33333.33'),D('0.00'),D('33333.33')))
        self.assertEqual((s['owner']['movements']['gross'],s['owner']['movements']['offsets'],s['owner']['closing']['due']),(D('20000.00'),D('8000.00'),D('12000.00')))   # the repair on 3 Sep is in the window
        self.assertEqual((s['driver']['movements']['earned'],s['driver']['movements']['paid']),(D('6666.67'),D('0.00')))
        self.assertEqual(s['operator']['accrual_margin'],D('6666.66'))

    def test_payments_before_from_change_only_the_opening_balance(self):
        s=self.st(date(2026,9,25),SEP30)
        self.assertEqual((s['customer']['opening']['due'],s['customer']['movements']['billed'],s['customer']['movements']['received'],s['customer']['closing']['due']),(D('10000.00'),D('20000.00'),D('0.00'),D('30000.00')))
        self.assertEqual((s['owner']['opening']['due'],s['owner']['closing']['due']),(D('0.00'),D('12000.00')))
        self.assertEqual((s['driver']['opening']['due'],s['driver']['closing']['due']),(D('1000.00'),D('5000.00')))

    def test_one_day_shows_its_own_movements(self):
        s=self.st(date(2026,9,20),date(2026,9,20))
        self.assertEqual((s['customer']['movements']['received'],s['owner']['movements']['paid'],s['driver']['movements']['paid']),(D('70000.00'),D('40000.00'),D('15000.00')))
        self.assertEqual(s['operator']['recorded_cash_movement'],D('15000.00'))     # 70,000 in, 40,000 and 15,000 out

    def test_every_day_of_the_month_adds_up_to_the_month_statement(self):
        month=services.statement(self.period);tot={}
        for i in range(30):
            d=SEP1+timedelta(days=i);s=self.st(d,d)
            for side,keys in(('customer',('billed','received')),('owner',('gross','offsets','paid')),('driver',('earned','advance','paid'))):
                for k in keys:tot[(side,k)]=tot.get((side,k),D(0))+s[side]['movements'][k]
        self.assertEqual(tot[('customer','billed')],month['customer']['billed']);self.assertEqual(tot[('customer','received')],month['customer']['received'])
        self.assertEqual((tot[('owner','gross')],tot[('owner','offsets')],tot[('owner','paid')]),(month['owner']['gross'],month['owner']['offsets'],month['owner']['paid']))
        self.assertEqual((tot[('driver','earned')],tot[('driver','paid')]),(month['driver']['earned'],month['driver']['paid']))

    def test_opening_plus_movements_equals_closing_for_any_range(self):
        for lo,hi in((SEP1,date(2026,9,10)),(date(2026,9,11),date(2026,9,19)),(date(2026,9,20),SEP30),(date(2026,8,15),date(2026,10,15)),(date(2026,9,5),date(2026,9,5))):
            s=self.st(lo,hi)
            for side in('customer','owner','driver'):
                self.assertEqual(s[side]['opening']['due']+s[side]['movements']['due'],s[side]['closing']['due'],(side,lo,hi))

    def test_consecutive_ranges_chain(self):
        a=self.st(SEP1,date(2026,9,14));b=self.st(date(2026,9,15),SEP30)
        for side in('customer','owner','driver'):self.assertEqual(a[side]['closing']['due'],b[side]['opening']['due'])
        self.assertEqual(b['customer']['closing']['due'],services.statement(self.period)['customer']['due'])

    def test_across_two_months(self):
        services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        s=self.st(date(2026,9,15),date(2026,10,15))
        self.assertEqual((s['customer']['opening']['due'],s['customer']['movements']['billed'],s['customer']['movements']['received'],s['customer']['closing']['due']),(D('46666.67'),D('101720.43'),D('70000.00'),D('78387.10')))

    def test_a_mid_month_revision_still_adds_back_to_the_month(self):
        services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,15),{'driver_salary':'25000'},'Raise')
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        total=sum(self.st(date(2026,10,d),date(2026,10,d))['driver']['movements']['earned'] for d in range(1,32))
        self.assertEqual(total,services.statement(october)['driver']['earned'])

    def test_a_month_that_is_not_issued_accrues_nothing(self):
        s=self.st(date(2026,10,1),date(2026,10,31))
        self.assertEqual((s['customer']['movements']['billed'],s['customer']['opening']['due'],s['customer']['closing']['due']),(D('0.00'),D('30000.00'),D('30000.00')))

    def test_bad_ranges_are_refused(self):
        with self.assertRaises(services.DomainError):self.st(date(2026,10,2),date(2026,10,1))
        with self.assertRaises(services.DomainError):self.st(date(2020,1,1),date(2026,10,1))

    def test_api_endpoint(self):
        c=Client();c.force_login(self.user);url=f'{self.base}/agreements/{self.agreement.pk}/statement'
        r=c.get(url+'?from=2026-09-25&to=2026-09-30');self.assertEqual(r.status_code,200,r.content);j=r.json()
        self.assertEqual((j['from'],j['to'],j['customer']['opening']['due'],j['customer']['movements']['billed'],j['customer']['closing']['due'],j['customer']['closing']['bill_state']),('2026-09-25','2026-09-30','10000.00','20000.00','30000.00','owed_to_us'))
        self.assertEqual(c.get(url+'?from=2026-09-25').status_code,422);self.assertEqual(c.get(url+'?from=2026-10-02&to=2026-10-01').status_code,422)
        self.assertEqual(self.as_viewer().get(url+'?from=2026-09-25&to=2026-09-30').status_code,200)

    def as_viewer(self):
        from django.contrib.auth import get_user_model
        c=Client();c.force_login(get_user_model().objects.get(username='viewer'));return c

class ReportRangeTests(RangeBase):
    def test_balance_reports_add_an_opening_column_and_use_the_closing_balance(self):
        r=self.rep('customer-balances','?from=2026-09-25&to=2026-09-30')
        self.assertEqual(r['column_ids'][4:],['billed','received','money_to_take','opening'])
        self.assertEqual(r['rows'][0][4:],['20000.00','0.00','30000.00','10000.00']);self.assertEqual(r['bill_states'],['owed_to_us'])
        o=self.rep('owner-liability','?from=2026-09-25&to=2026-09-30');self.assertEqual(o['rows'][0][4:],['12000.00','0.00','0.00','12000.00','0.00'])
        d=self.rep('driver-payroll','?from=2026-09-25&to=2026-09-30');self.assertEqual((d['rows'][0][-1],d['rows'][0][-2]),('1000.00','5000.00'))

    def test_income_reports_the_window_flows_and_the_closing_outstanding(self):
        r=self.rep('income','?from=2026-09-01&to=2026-09-10')
        self.assertEqual(r['rows'][0][4:],['33333.33','0.00','33333.33','20000.00','6666.67','0.00','6666.66'])

    def test_an_old_unpaid_month_appears_as_an_opening_balance(self):
        r=self.rep('customer-balances','?from=2026-11-01');self.assertEqual(r['count'],1)
        self.assertEqual(r['rows'][0][4:],['0.00','0.00','30000.00','30000.00'])
        self.assertEqual(self.rep('customer-balances','?from=2026-11-01&to=2026-11-30')['rows'][0][-1],'30000.00')

    def test_settled_months_with_no_movement_do_not_clutter_the_report(self):
        self.pay('customer_payment','30000','R-FIN')
        self.assertEqual(self.rep('customer-balances','?from=2026-11-01')['count'],0)         # fully paid, nothing to carry into the range
        self.assertEqual(self.rep('customer-balances','?from=2026-09-01&to=2026-09-30')['count'],1)

    def test_no_filter_keeps_the_old_columns(self):
        self.assertEqual(self.rep('customer-balances')['column_ids'][4:],['billed','received','money_to_take'])

    def test_aging_counts_balances_as_of_the_date(self):
        r=self.rep('customer-aging','?as_of=2026-09-15');self.assertEqual((r['rows'][0][1],r['rows'][0][-1]),('50000.00',0))   # half the month accrued, nothing paid yet
        r=self.rep('customer-aging','?as_of=2026-10-20');self.assertEqual((r['rows'][0][2],r['rows'][0][-1]),('30000.00',20))
        r=self.rep('customer-aging','?from=2026-09-01&to=2026-10-20');self.assertEqual(r['rows'][0][-1],20)         # a range ages as of its end

    def test_export_carries_the_range_and_the_opening_column(self):
        r=self.c.get(f'{self.base}/reports/customer-balances/export?from=2026-09-25&to=2026-09-30');self.assertEqual(r.status_code,200)
        text=r.content.decode('utf-8-sig');self.assertIn('from=2026-09-25',text);self.assertIn('to=2026-09-30',text);self.assertIn('Balance at the start (৳)',text);self.assertIn('10000.00',text)

    def test_query_count_is_constant_in_periods(self):
        services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        with CaptureQueriesContext(connection) as few:self.rep('customer-balances','?from=2026-09-01&to=2026-10-31')
        for m in(11,12):services.issue_period(self.org,self.agreement.pk,date(2026,m,1))
        with CaptureQueriesContext(connection) as many:self.rep('customer-balances','?from=2026-09-01&to=2026-12-31')
        self.assertEqual(len(few),len(many))

    def test_the_month_filters_keep_their_whole_month_meaning(self):
        legacy=self.rep('customer-balances','?month=2026-09-01');exact=self.rep('customer-balances','?from=2026-09-01&to=2026-09-30')
        self.assertEqual(legacy['column_ids'][4:],['billed','received','money_to_take'])                  # no opening column: still the period view
        self.assertEqual(legacy['rows'][0][4:],exact['rows'][0][4:7])                                           # and the same figures for a whole month
