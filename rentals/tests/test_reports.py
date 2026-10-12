import csv
import io
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from ..models import AuditEvent, Agreement, Document, Expense, Membership, Organization, Party, Vehicle
from .. import services
from .base import RentalsTestCase

D=Decimal

class ReportBase(RentalsTestCase):
    def setUp(self):
        super().setUp()
        self.base=f'/api/v1/orgs/{self.org.pk}';self.c=Client();self.c.force_login(self.user)
        U=get_user_model()
        for role in('viewer','operations'):
            u=U.objects.create_user(role,password='x');Membership.objects.create(organization=self.org,user=u,role=role)
        self.worked()

    def worked(self):
        e=services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'repair','amount':D(8000),'paid_by':'operator','responsible':'owner','offset_owner':True,'reference':'GAR-0182','event_date':date(2026,9,3)},'g')
        services.approve_expense(self.org,self.user,e.pk)
        self.pay('customer_payment','70000','R-C');self.pay('owner_payout','40000','R-O');self.pay('driver_payout','15000','R-D')

    def second_fleet(self,month=date(2026,9,1)):
        """A second vehicle with its own owner, customer and driver, so filters have something to exclude."""
        o=Party.objects.create(organization=self.org,kind='owner',name='Second Owner');cu=Party.objects.create(organization=self.org,kind='customer',name='Second Customer');d=Party.objects.create(organization=self.org,kind='driver',name='Second Driver')
        car=Vehicle.objects.create(organization=self.org,registration='CTG-77',model_name='Honda',owner=o)
        a=Agreement(organization=self.org,vehicle=car,customer=cu,driver=d,start_date=date(2026,1,1),customer_rent=D(50000),owner_rent=D(30000),driver_salary=D(10000))
        a,_=services.create_agreement(self.org,self.user,a)
        p=services.issue_period(self.org,a.pk,month)
        return o,cu,d,car,a,p

    def rep(self,rtype,qs=''):
        r=self.c.get(f'{self.base}/reports/{rtype}{qs}');self.assertEqual(r.status_code,200,r.content);return r.json()

class PeriodReportTests(ReportBase):
    def test_five_period_reports_match_frontend_columns_and_worked_example(self):
        cases={'vehicle-contribution':(['Agreement','Vehicle','Customer','Month','Earned rent BDT','Owner gross BDT','Driver salary BDT','Operator costs BDT','Accrual margin BDT'],['100000.00','60000.00','20000.00','0.00','20000.00']),
            'customer-balances':(['Agreement','Vehicle','Customer','Month','Billed BDT','Received BDT','Outstanding BDT'],['100000.00','70000.00','30000.00']),
            'owner-liability':(['Agreement','Vehicle','Owner','Month','Gross BDT','Offsets BDT','Paid BDT','Due BDT'],['60000.00','8000.00','40000.00','12000.00']),
            'driver-payroll':(['Agreement','Vehicle','Driver','Month','Earned BDT','Advance BDT','Paid BDT','Due BDT'],['20000.00','0.00','15000.00','5000.00']),
            'cash-movement':(['Agreement','Vehicle','Customer','Month','Receipts BDT','Owner paid BDT','Driver paid BDT','Net cash BDT'],['70000.00','40000.00','15000.00','7000.00'])}
        for rtype,(columns,values) in cases.items():
            r=self.rep(rtype);self.assertEqual(r['columns'],columns,rtype);self.assertEqual(r['count'],1)
            row=r['rows'][0];self.assertEqual(row[:2],[f'AGR-{self.agreement.pk}','DHA-19-7724']);self.assertEqual(row[3],'2026-09-01');self.assertEqual(row[4:],values,rtype)
            self.assertEqual(list(r['totals'].values()),values,rtype)
        self.assertEqual(self.rep('owner-liability')['rows'][0][2],'Farhana Rahman');self.assertEqual(self.rep('driver-payroll')['rows'][0][2],'Imran Hossain')

    def test_reports_reconcile_to_statements_across_periods(self):
        self.second_fleet();services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        periods=list(self.org.period_set.all());stmts=[services.statement(p) for p in periods]
        self.assertEqual(self.rep('owner-liability')['totals']['Due BDT'],str(sum((s['owner']['due'] for s in stmts),D(0)).quantize(D('0.01'))))
        self.assertEqual(self.rep('customer-balances')['totals']['Outstanding BDT'],str(sum((s['customer']['due'] for s in stmts),D(0)).quantize(D('0.01'))))
        self.assertEqual(self.rep('vehicle-contribution')['totals']['Accrual margin BDT'],str(sum((s['operator']['accrual_margin'] for s in stmts),D(0)).quantize(D('0.01'))))
        self.assertEqual(self.rep('cash-movement')['count'],len(periods))

    def test_filters(self):
        o,cu,d,car,a,p=self.second_fleet();october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        self.assertEqual(self.rep('customer-balances')['count'],3)
        self.assertEqual(self.rep('customer-balances','?month=2026-09-01')['count'],2)
        self.assertEqual(self.rep('customer-balances','?month_from=2026-10-01')['count'],1)
        self.assertEqual(self.rep('customer-balances','?month_to=2026-09-01')['count'],2)
        self.assertEqual(self.rep('customer-balances',f'?agreement_id={a.pk}')['rows'][0][2],'Second Customer')
        self.assertEqual(self.rep('customer-balances',f'?vehicle_id={self.car.pk}')['count'],2)
        self.assertEqual(self.rep('owner-liability',f'?owner_id={o.pk}')['rows'][0][2],'Second Owner')
        self.assertEqual(self.rep('driver-payroll',f'?driver_id={self.driver.pk}')['count'],2)
        self.assertEqual(self.rep('customer-balances',f'?customer_id={cu.pk}')['count'],1)
        self.assertEqual(self.rep('customer-balances',f'?owner_id={o.pk}&month=2026-10-01')['count'],0)

    def test_owner_change_shows_the_owner_of_that_month(self):
        new_owner=Party.objects.create(organization=self.org,kind='owner',name='New Owner')
        services.transfer_vehicle_ownership(self.org,self.user,self.car.pk,new_owner.pk,date(2026,10,1));services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        names={r[3]:r[2] for r in self.rep('owner-liability')['rows']}
        self.assertEqual(names,{'2026-09-01':'Farhana Rahman','2026-10-01':'New Owner'})

    def test_pagination_envelope(self):
        for m in range(10,13):services.issue_period(self.org,self.agreement.pk,date(2026,m,1))
        r=self.rep('customer-balances','?limit=2');self.assertEqual((r['count'],len(r['rows']),r['next']),(4,2,2));self.assertEqual(r['rows'][0][3],'2026-12-01')
        r2=self.rep('customer-balances','?limit=2&offset=2');self.assertEqual((len(r2['rows']),r2['next']),(2,None))
        self.assertEqual(r['totals'],self.rep('customer-balances')['totals'])                                       # totals cover every row, not just the page
        self.assertEqual(len(self.rep('customer-balances','?limit=0')['rows']),1)                                    # clamped to 1

    def test_query_count_does_not_grow_with_periods(self):
        def queries(rtype):
            with CaptureQueriesContext(connection) as q:self.rep(rtype)
            return len(q)
        before={t:queries(t) for t in('vehicle-contribution','owner-liability','customer-aging','cost-by-responsible','missing-receipts')}
        self.second_fleet()
        for y,m in((2026,10),(2026,11),(2026,12),(2027,1),(2027,2),(2027,3)):services.issue_period(self.org,self.agreement.pk,date(y,m,1))
        after={t:queries(t) for t in before}
        self.assertEqual(before,after)
        self.assertLessEqual(max(after.values()),12)

class OtherReportTests(ReportBase):
    def test_cost_by_responsible_posted_only(self):
        for ref,cat,amount,resp in(('C1','fuel','900','owner'),('C2','toll','300','customer'),('C3','cleaning','200','operator')):
            services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':cat,'amount':D(amount),'paid_by':'operator','responsible':resp,'offset_owner':False,'reference':ref,'event_date':date(2026,9,5)},ref)
        gone=services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'fuel','amount':D(450),'paid_by':'operator','responsible':'owner','offset_owner':False,'reference':'C4','event_date':date(2026,9,6)},'c4');services.reverse_expense(self.org,self.user,gone.pk,'dup')
        r=self.rep('cost-by-responsible');rows={(x[0],x[1]):x[3] for x in r['rows']}
        self.assertEqual(rows,{('Customer','Toll'):'300.00',('Operator','Cleaning'):'200.00',('Owner','Fuel'):'900.00',('Owner','Repair'):'8000.00'})
        self.assertEqual(r['totals'],{'Amount BDT':'9400.00'})

    def test_customer_aging_buckets(self):
        def bucket(as_of):
            r=self.rep('customer-aging',f'?as_of={as_of}');row=r['rows'][0];return {c.replace(' BDT',''):v for c,v in zip(r['columns'][1:],row[1:]) if v!='0.00' and c!='Days overdue'}
        self.assertEqual(bucket('2026-09-15'),{'Current':'50000.00','Total':'50000.00'})          # M13c: the balance on that date, with half the month accrued
        self.assertEqual(bucket('2026-10-20'),{'1-30 days':'30000.00','Total':'30000.00'})
        self.assertEqual(bucket('2026-12-15'),{'61-90 days':'30000.00','Total':'30000.00'})
        self.assertEqual(bucket('2027-03-01'),{'Over 90 days':'30000.00','Total':'30000.00'})
        self.pay('customer_payment','30000','AGE-FULL');self.assertEqual(self.rep('customer-aging')['rows'],[])

    def test_missing_receipts(self):
        base={'period_id':self.period.pk,'category':'fuel','paid_by':'operator','responsible':'owner','offset_owner':False,'event_date':date(2026,9,7)}
        bare=services.record_expense(self.org,self.user,{**base,'amount':D(100),'reference':'NR-1'},'nr1')
        with_ref=services.record_expense(self.org,self.user,{**base,'amount':D(110),'reference':'NR-2','receipt_reference':'BILL-1'},'nr2')
        with_doc=services.record_expense(self.org,self.user,{**base,'amount':D(120),'reference':'NR-3'},'nr3')
        Document.objects.create(organization=self.org,target_type='expense',target_id=str(with_doc.pk),storage_key='x/1.pdf',original_name='a.pdf',content_type='application/pdf',size=1,sha256='0'*64,uploaded_by=self.user)
        draft=services.record_expense(self.org,self.user,{**base,'amount':D(130),'reference':'NR-4','draft':True},'nr4')
        reversed_=services.record_expense(self.org,self.user,{**base,'amount':D(140),'reference':'NR-5'},'nr5');services.reverse_expense(self.org,self.user,reversed_.pk,'x')
        ids={r[0] for r in self.rep('missing-receipts')['rows']}
        self.assertIn(bare.public_id,ids);self.assertTrue({with_ref.public_id,with_doc.public_id,draft.public_id,reversed_.public_id}.isdisjoint(ids))
        self.assertIn('GAR-0182',{r[6] for r in self.rep('missing-receipts')['rows']})                                # the worked repair has no receipt either

    def test_entries_report_matches_frontend_export_columns(self):
        r=self.rep('entries');self.assertEqual(r['columns'],['ID','Agreement','Date','Type','Amount BDT','Paid by','Responsible','Status','Reference'])
        types=[x[3] for x in r['rows']];self.assertEqual(sorted(set(types)),['Customer payment','Driver payout','Expense','Owner payout']);self.assertEqual(r['count'],4)
        exp=next(x for x in r['rows'] if x[3]=='Expense');self.assertEqual((exp[4],exp[5],exp[6],exp[7],exp[8]),('8000.00','Operator','Owner','Posted','GAR-0182'))

    def test_csv_neutralizes_cells_excel_would_run_as_formulas(self):
        from ..models import Expense
        from ..services.reports import excel_safe
        formula='=HYPERLINK("http://x","click")'
        Expense.objects.filter(reference='GAR-0182').update(reference=formula)
        body=self.c.get(f'{self.base}/reports/entries/export').content.decode()
        self.assertTrue(body.startswith('﻿'))
        refs={r[8] for r in csv.reader(io.StringIO(body.lstrip('﻿'))) if len(r)==9}
        self.assertIn("'"+formula,refs);self.assertNotIn(formula,refs)
        self.assertEqual([excel_safe(x) for x in ['+88017','@a','-x','-1500.00','-.5','12','',-5,'	z']],["'+88017","'@a","'-x",'-1500.00','-.5','12','',-5,"'	z"])

    def test_unknown_report_and_isolation(self):
        self.assertEqual(self.c.get(f'{self.base}/reports/nope').status_code,422)
        other=Organization.objects.create(name='Other');U=get_user_model().objects.create_user('rep9',password='x');Membership.objects.create(organization=other,user=U,role='admin')
        oc=Client();oc.force_login(U);r=oc.get(f'/api/v1/orgs/{other.pk}/reports/customer-balances').json();self.assertEqual((r['count'],r['rows']),(0,[]))
        self.assertEqual(oc.get(f'/api/v1/orgs/{self.org.pk}/reports/customer-balances').status_code,404)
        types=self.c.get(f'{self.base}/report-types').json();self.assertEqual(len(types),12);self.assertIn('Vehicle contribution',[t['title'] for t in types])

class ExportTests(ReportBase):
    def test_csv_has_preamble_filters_header_rows_and_generated_at(self):
        o,cu,d,car,a,p=self.second_fleet()
        r=self.c.get(f'{self.base}/reports/owner-liability/export?month=2026-09-01&owner_id={self.owner.pk}')
        self.assertEqual(r.status_code,200);self.assertIn('text/csv',r['Content-Type']);self.assertIn('attachment',r['Content-Disposition']);self.assertEqual(r['Cache-Control'],'private, no-store')
        text=r.content.decode('utf-8');self.assertTrue(text.startswith('﻿'))
        rows=list(csv.reader(io.StringIO(text.lstrip('﻿'))))
        self.assertEqual(rows[0],['Report','Owner liability']);self.assertEqual(rows[1][0],'Generated at');self.assertTrue(rows[1][1])
        self.assertEqual(rows[2],['Filters',f'month=2026-09-01; owner_id={self.owner.pk}']);self.assertEqual(rows[3],[])
        self.assertEqual(rows[4],['Agreement','Vehicle','Owner','Month','Gross BDT','Offsets BDT','Paid BDT','Due BDT','Generated at'])
        self.assertEqual(len(rows[5:]),1);self.assertEqual(rows[5][2],'Farhana Rahman');self.assertEqual(rows[5][4:8],['60000.00','8000.00','40000.00','12000.00']);self.assertTrue(rows[5][8])

    def test_export_returns_every_row_not_one_page(self):
        for m in range(10,13):services.issue_period(self.org,self.agreement.pk,date(2026,m,1))
        rows=list(csv.reader(io.StringIO(self.c.get(f'{self.base}/reports/customer-balances/export').content.decode().lstrip('﻿'))))
        self.assertEqual(len(rows)-5,4)

    def test_entries_export_uses_frontend_header_without_extra_column(self):
        rows=list(csv.reader(io.StringIO(self.c.get(f'{self.base}/reports/entries/export').content.decode().lstrip('﻿'))))
        self.assertEqual(rows[4],['ID','Agreement','Date','Type','Amount BDT','Paid by','Responsible','Status','Reference']);self.assertEqual(len(rows)-5,4)

    def test_export_permission_audit_and_filters_preserved_in_audit(self):
        for role,status in(('viewer',200),('operations',403)):
            c=Client();c.force_login(get_user_model().objects.get(username=role));self.assertEqual(c.get(f'{self.base}/reports/customer-balances/export').status_code,status,role)
        own=get_user_model().objects.create_user('own7',password='x');services.grant_party_access(self.org,self.owner.pk,'own7');oc=Client();oc.force_login(own)
        for path in('/reports/customer-balances','/reports/customer-balances/export','/report-types'):self.assertEqual(oc.get(f'{self.base}{path}').status_code,403,path)
        self.c.get(f'{self.base}/reports/customer-balances/export?month=2026-09-01')
        ev=AuditEvent.objects.filter(action='report.export').latest('id');self.assertEqual((ev.target_id,ev.after['filters']),('customer-balances',{'month':'2026-09-01'}))

class PaginatedListsTests(ReportBase):
    def test_every_unbounded_list_uses_count_items_next(self):
        for path in('/parties','/vehicles','/agreements','/periods','/expenses','/payments','/notifications','/charges','/invoices','/settlements','/maintenance-cases'):
            r=self.c.get(f'{self.base}{path}');self.assertEqual(r.status_code,200,path)
            body=r.json();self.assertEqual(set(body)-{'totals'},{'count','items','next'},path);self.assertIsInstance(body['items'],list,path)
        r=self.c.get(f'{self.base}/payments?limit=2').json();self.assertEqual((r['count'],len(r['items']),r['next']),(3,2,2))
        r=self.c.get(f'{self.base}/payments?limit=2&offset=2').json();self.assertEqual((len(r['items']),r['next']),(1,None))
        self.assertEqual(len(self.c.get(f'{self.base}/parties?limit=9999').json()['items']),3)                       # capped at 200, not an error
        for i in range(205):Party.objects.create(organization=self.org,kind='customer',name=f'Bulk {i:03d}')
        big=self.c.get(f'{self.base}/parties?limit=9999').json();self.assertEqual((big['count'],len(big['items']),big['next']),(208,200,200))
        self.assertEqual(len(self.c.get(f'{self.base}/parties?limit=200&offset=200').json()['items']),8)
