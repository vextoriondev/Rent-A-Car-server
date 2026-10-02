from datetime import date
from decimal import Decimal
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from ..models import Account, Charge, Invoice, JournalEntry, JournalLine, Membership, Obligation
from .. import services
from .base import RentalsTestCase

D=Decimal

class LedgerBase(RentalsTestCase):
    def charge(self,side,kind,amount,direction=None,key=None,period=None,**kw):
        data={'period_id':(period or self.period).pk,'side':side,'kind':kind,'amount':D(amount),'direction':direction,**kw}
        return services.post_charge(self.org,self.user,data,key or f'{side}-{kind}-{amount}-{direction}-{kw}')

    def assertReconciles(self,period=None):
        p=period or self.period
        self.assertEqual(services.diff_statements(services.ledger_statement(p),services.obligation_statement(p)),[])

    def advance(self,amount,ref):
        return services.record_payment(self.org,self.user,{'kind':'salary_advance','amount':D(amount),'reference':ref,'method':'cash','event_date':date(2026,9,10),'allocations':[{'obligation_id':self.ob['driver_salary'].pk,'amount':D(amount)}]},ref)

    def expense(self,ref,amount='8000',offset=True,responsible='owner',category='repair'):
        e=services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':category,'amount':D(amount),'paid_by':'operator','responsible':responsible,'offset_owner':offset,'reference':ref,'event_date':date(2026,9,3)},ref)
        if e.state=='pending':services.approve_expense(self.org,self.user,e.pk)
        e.refresh_from_db();return e

class ChartAndReconciliationTests(LedgerBase):
    def test_chart_of_accounts_and_dimensions(self):
        codes=set(Account.objects.filter(organization=self.org).values_list('code',flat=True))
        self.assertTrue({'CASH','CUSTOMER_RECEIVABLE','OWNER_PAYABLE','DRIVER_PAYABLE','OTHER_REVENUE','OPENING_BALANCE'}<=codes)
        self.assertFalse(JournalLine.objects.filter(entry__organization=self.org,account_ref__isnull=True).exists())
        line=JournalLine.objects.get(entry__source_type='obligation',account='OWNER_PAYABLE')
        self.assertEqual((line.party,line.period,line.agreement,line.vehicle),(self.owner,self.period,self.agreement,self.car))

    def test_worked_example_reconciles_and_invariants_hold(self):
        self.expense('GAR-0182')
        self.pay('customer_payment','40000','C1');self.pay('customer_payment','30000','C2');self.pay('owner_payout','40000','O1');self.pay('driver_payout','15000','D1')
        s=services.statement(self.period);self.assertReconciles()
        self.assertEqual((s['customer']['due'],s['owner']['due'],s['driver']['due'],s['operator']['accrual_margin'],s['operator']['recorded_cash_movement']),(D(30000),D(12000),D(5000),D(20000),D(7000)))
        out=StringIO();call_command('check_ledger',stdout=out);self.assertIn('Ledger OK',out.getvalue())

    def test_reconciles_through_partials_reversal_advance_credits_and_charges(self):
        steps=[]
        self.charge('customer','charge',5000);steps.append('charge')
        self.assertReconciles();self.assertEqual(services.statement(self.period)['customer']['due'],D(105000))
        self.charge('customer','credit',2000);self.assertReconciles()
        self.charge('owner','adjustment',1000,'increase');self.charge('driver','charge',500);self.assertReconciles()
        e=self.expense('REC-1');self.assertReconciles()
        self.pay('customer_payment','40000','R-C1');self.assertReconciles()
        self.pay('owner_payout','25000','R-O1');self.assertReconciles()
        self.advance('2000','R-ADV');self.assertReconciles()
        services.reverse_expense(self.org,self.user,e.pk,'wrong car');self.assertReconciles()
        s=services.statement(self.period)
        self.assertEqual((s['customer']['billed'],s['customer']['due']),(D(103000),D(63000)))
        self.assertEqual((s['owner']['gross'],s['owner']['offsets'],s['owner']['paid'],s['owner']['due']),(D(61000),D(0),D(25000),D(36000)))
        self.assertEqual((s['driver']['earned'],s['driver']['paid'],s['driver']['advance'],s['driver']['due']),(D(20500),D(2000),D(2000),D(18500)))
        self.assertEqual(s['operator']['accrual_margin'],D(103000)-D(61000)-D(20500))
        call_command('check_ledger',stdout=StringIO())

    def test_advance_is_visible_separately_and_driver_paid_semantics_kept(self):
        self.advance('3000','ADV-1');self.pay('driver_payout','15000','DP-1')
        d=services.statement(self.period)['driver']
        self.assertEqual((d['paid'],d['advance'],d['due']),(D(18000),D(3000),D(2000)))
        self.assertEqual(JournalEntry.objects.filter(source_type='payment_advance').count(),1)
        self.assertReconciles()

    def test_owner_owes_operator_flag(self):
        self.charge('owner','adjustment',70000,'decrease')
        o=services.statement(self.period)['owner'];self.assertEqual(o['due'],D(-10000));self.assertTrue(o['owner_owes_operator'])
        self.assertFalse(services.statement(self.period)['customer'].get('owner_owes_operator',False));self.assertReconciles()

class ChargeTests(LedgerBase):
    def test_customer_charge_credit_and_margin(self):
        c=self.charge('customer','charge',5000);self.assertEqual((c.obligation.amount,c.obligation.party),(D(5000),self.customer))
        cr=self.charge('customer','credit',2000);self.assertEqual(cr.obligation.amount,D(-2000))
        s=services.statement(self.period);self.assertEqual((s['customer']['billed'],s['operator']['accrual_margin']),(D(103000),D(23000)))

    def test_direction_rules(self):
        with self.assertRaises(services.DomainError):self.charge('customer','charge',1,'decrease')
        with self.assertRaises(services.DomainError):self.charge('customer','credit',1,'increase')
        with self.assertRaises(services.DomainError):self.charge('customer','adjustment',1)            # needs a direction
        with self.assertRaises(services.DomainError):self.charge('customer','carry_out',1,'decrease')
        with self.assertRaises(services.DomainError):self.charge('driver','reimbursement',1,'increase')
        with self.assertRaises(services.DomainError):self.charge('customer','charge',-5)

    def test_idempotent_and_closed_period_blocked(self):
        a=self.charge('customer','charge',100,key='same');b=self.charge('customer','charge',100,key='same')
        self.assertEqual(a.pk,b.pk);self.assertEqual(Charge.objects.count(),1)
        with self.assertRaises(services.DomainError):self.charge('customer','charge',999,key='same')
        services.close_period(self.org,self.user,self.period.pk)
        with self.assertRaises(services.DomainError):self.charge('customer','charge',1,key='late')

    def test_customer_reimbursement_converts_recoverable_to_billed(self):
        self.expense('CUST-EXP','3000',offset=False,responsible='customer',category='cleaning')
        self.assertEqual(services.statement(self.period)['customer']['separate_recoverable'],D(3000))
        with self.assertRaises(services.DomainError):self.charge('customer','reimbursement',4000,'increase')
        self.charge('customer','reimbursement',3000,'increase')
        s=services.statement(self.period)['customer'];self.assertEqual((s['separate_recoverable'],s['due']),(D(0),D(103000)));self.assertReconciles()

    def test_owner_reimbursement_reduces_payable(self):
        self.expense('OWN-EXP','2500',offset=False)
        self.charge('owner','reimbursement',2500,'decrease')
        o=services.statement(self.period)['owner'];self.assertEqual((o['separate_recoverable'],o['due']),(D(0),D(57500)));self.assertReconciles()

    def test_credit_caps_what_can_be_paid(self):
        self.charge('customer','credit',10000)
        with self.assertRaises(services.DomainError):self.pay('customer_payment','95000','TOO-MUCH')    # due is 90,000 although the rent obligation is 100,000
        self.pay('customer_payment','90000','JUST-RIGHT');self.assertEqual(services.statement(self.period)['customer']['due'],D(0))

    def test_charge_obligation_can_be_paid_and_negative_one_cannot(self):
        c=self.charge('customer','charge',4000);cr=self.charge('customer','credit',500)
        services.record_payment(self.org,self.user,{'kind':'customer_payment','amount':D(4000),'reference':'CH-P','method':'bank','event_date':date(2026,9,20),'allocations':[{'obligation_id':c.obligation_id,'amount':D(4000)}]},'chp')
        with self.assertRaises(services.DomainError):services.record_payment(self.org,self.user,{'kind':'customer_payment','amount':D(100),'reference':'NEG','method':'bank','event_date':date(2026,9,20),'allocations':[{'obligation_id':cr.obligation_id,'amount':D(100)}]},'neg')
        self.assertReconciles()

    def test_three_original_kinds_stay_unique_per_period(self):
        from django.db import IntegrityError,transaction
        with self.assertRaises(IntegrityError),transaction.atomic():Obligation.objects.create(period=self.period,kind='owner_rent',party=self.owner,amount=D(1))
        self.charge('customer','charge',1,key='a');self.charge('customer','charge',1,key='b')       # many charges per period are fine
        self.assertEqual(self.period.obligations.filter(kind='charge').count(),2)

class CarryForwardTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.pay('customer_payment','70000','CF-C');self.pay('owner_payout','40000','CF-O');self.pay('driver_payout','15000','CF-D')
        self.october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))

    def test_requires_closed_previous_period(self):
        with self.assertRaises(services.DomainError):services.carry_forward(self.org,self.user,self.october.pk)
        with self.assertRaises(services.DomainError):services.carry_forward(self.org,self.user,self.period.pk)        # no August

    def test_carry_forward_moves_dues_without_changing_totals(self):
        services.close_period(self.org,self.user,self.period.pk)
        before=sum(services.statement(p)[s]['due'] for p in (self.period,self.october) for s in('customer','owner','driver'))
        posted=services.carry_forward(self.org,self.user,self.october.pk);self.assertEqual(len(posted),6)
        sep,octo=services.statement(self.period),services.statement(self.october)
        self.assertEqual((sep['customer']['due'],sep['customer']['carried_forward']),(D(0),D(30000)))
        self.assertEqual((sep['owner']['due'],sep['owner']['carried_forward'],sep['driver']['due']),(D(0),D(20000),D(0)))
        self.assertEqual((octo['customer']['billed'],octo['customer']['due'],octo['owner']['due'],octo['driver']['due']),(D(130000),D(130000),D(80000),D(25000)))
        self.assertEqual(before,sum(services.statement(p)[s]['due'] for p in (self.period,self.october) for s in('customer','owner','driver')))
        self.assertReconciles(self.period);self.assertReconciles(self.october)
        self.assertEqual(sep['operator']['accrual_margin'],D(20000))                                   # carry never touches margin
        self.assertEqual(services.statement(self.october)['operator']['accrual_margin'],D(20000))
        call_command('check_ledger',stdout=StringIO())

    def test_carry_forward_is_idempotent_and_october_dues_are_payable(self):
        services.close_period(self.org,self.user,self.period.pk)
        services.carry_forward(self.org,self.user,self.october.pk);self.assertEqual(services.carry_forward(self.org,self.user,self.october.pk),[])
        self.assertEqual(Charge.objects.filter(kind='opening_balance').count(),3)
        opening=Obligation.objects.get(period=self.october,kind='opening_balance',party=self.customer)
        services.record_payment(self.org,self.user,{'kind':'customer_payment','amount':D(30000),'reference':'OCT-OPEN','method':'bank','event_date':date(2026,10,5),'allocations':[{'obligation_id':opening.pk,'amount':D(30000)}]},'octopen')
        self.assertEqual(services.statement(self.october)['customer']['due'],D(100000))

class InvoiceTests(LedgerBase):
    def issue(self,key='inv-1',**kw):
        return services.issue_invoice(self.org,self.user,{'period_id':self.period.pk,**kw},key)

    def test_preview_issue_number_and_idempotency(self):
        pre=services.preview_invoice(self.org,self.period.pk);self.assertEqual((len(pre['lines']),pre['total']),(1,D(100000)))
        inv=self.issue(issue_date=date(2026,9,30))
        self.assertEqual((inv.number,inv.party,inv.due_date),('INV-2026-00001',self.customer,date(2026,10,15)))
        self.assertEqual(self.issue(issue_date=date(2026,9,30)).pk,inv.pk)                                                       # same key: same invoice
        with self.assertRaises(services.DomainError):self.issue('inv-2')                               # nothing left to invoice
        self.charge('customer','charge',5000);inv2=self.issue('inv-3');self.assertEqual((inv2.number,services.invoice_summary(inv2)['amount']),('INV-%d-00002'%date.today().year,D(5000)))

    def test_status_is_derived_from_allocations(self):
        inv=self.issue(due_date=date(2026,10,15));today=date(2026,10,1)
        self.assertEqual(services.invoice_summary(inv,today)['status'],'issued')
        self.pay('customer_payment','40000','I-P1');s=services.invoice_summary(inv,today);self.assertEqual((s['status'],s['paid'],s['outstanding']),('partially_paid',D(40000),D(60000)))
        self.assertEqual(services.invoice_summary(inv,date(2026,10,16))['status'],'overdue')
        self.pay('customer_payment','60000','I-P2');self.assertEqual(services.invoice_summary(inv,date(2026,10,16))['status'],'paid')
        with self.assertRaises(Exception):Invoice.objects.filter(pk=inv.pk).update(status='paid')    # no stored status to edit

    def test_void_rules_and_reinvoicing(self):
        inv=self.issue();self.pay('customer_payment','1000','V-P')
        with self.assertRaises(services.DomainError):services.void_invoice(self.org,self.user,inv.pk,'oops')
        c=self.charge('customer','charge',700);inv2=self.issue('inv-x',obligation_ids=[c.obligation_id])
        voided=services.void_invoice(self.org,self.user,inv2.pk,'wrong customer');self.assertEqual(services.invoice_summary(voided)['status'],'void')
        again=self.issue('inv-y',obligation_ids=[c.obligation_id]);self.assertNotEqual(again.pk,inv2.pk)
        with self.assertRaises(services.DomainError):services.void_invoice(self.org,self.user,again.pk,'')

    def test_credit_lines_reduce_total_and_carry_out_never_invoiced(self):
        c=self.charge('customer','credit',2000);inv=self.issue()
        self.assertEqual(services.invoice_summary(inv)['amount'],D(98000))
        with self.assertRaises(services.DomainError):services.issue_invoice(self.org,self.user,{'period_id':self.period.pk,'obligation_ids':[self.ob['owner_rent'].pk]},'owner-line')

class LedgerApiTests(LedgerBase):
    def test_api_charge_ledger_invoice_and_permissions(self):
        c=Client();c.force_login(self.user);base=f'/api/v1/orgs/{self.org.pk}'
        r=c.post(f'{base}/charges',{'period_id':self.period.pk,'side':'customer','kind':'charge','amount':'1500','description':'Late return'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='api-c1');self.assertEqual(r.status_code,200,r.content);self.assertEqual(r.json()['direction'],'increase')
        led=c.get(f'{base}/periods/{self.period.pk}/ledger').json();self.assertTrue(led['reconciles']);self.assertEqual(led['statement']['customer']['due'],'101500.00')
        self.assertEqual(len(c.get(f'{base}/accounts').json()),12)
        pre=c.get(f'{base}/periods/{self.period.pk}/invoice-preview').json();self.assertEqual(pre['total'],'101500.00')
        inv=c.post(f'{base}/invoices',{'period_id':self.period.pk},content_type='application/json',HTTP_IDEMPOTENCY_KEY='api-i1').json();self.assertEqual((inv['status'],inv['amount']),('issued','101500.00'))
        self.assertEqual(c.get(f'{base}/invoices/{inv["id"]}').json()['number'],inv['number'])
        self.assertEqual(c.post(f'{base}/invoices/{inv["id"]}/void',{'reason':'test'},content_type='application/json').json()['status'],'void')
        body={'period_id':self.period.pk,'side':'customer','kind':'charge','amount':'1'}
        User=get_user_model();ops=User.objects.create_user('ops2',password='x');Membership.objects.create(organization=self.org,user=ops,role='operations')
        oc=Client();oc.force_login(ops)
        self.assertEqual(oc.post(f'{base}/charges',body,content_type='application/json',HTTP_IDEMPOTENCY_KEY='x').status_code,403)
        self.assertEqual(oc.get(f'{base}/periods/{self.period.pk}/ledger').status_code,403)
        own=User.objects.create_user('own2',password='x');services.grant_party_access(self.org,self.owner.pk,'own2')
        ac=Client();ac.force_login(own)
        for path in ('/invoices','/charges',f'/periods/{self.period.pk}/ledger','/accounts'):self.assertEqual(ac.get(f'{base}{path}').status_code,403,path)

class CheckLedgerCommandTests(LedgerBase):
    def test_detects_unbalanced_entry_and_disagreement(self):
        entry=JournalEntry.objects.create(organization=self.org,source_type='manual',source_id='x')
        JournalLine.objects.create(entry=entry,account='CASH',account_ref=Account.objects.get(organization=self.org,code='CASH'),debit=D(10),credit=D(0))
        with self.assertRaises(SystemExit):call_command('check_ledger',stdout=StringIO(),stderr=StringIO())
        entry.lines.all().delete();entry.delete()
        JournalLine.objects.create(entry=JournalEntry.objects.create(organization=self.org,source_type='manual',source_id='y'),account='CASH',account_ref=Account.objects.get(organization=self.org,code='CASH'),debit=D(5),credit=D(5),period=self.period)
        call_command('check_ledger',stdout=StringIO())
        Obligation.objects.filter(pk=self.ob['owner_rent'].pk).update(amount=D(1))                      # corrupt the obligations side only
        with self.assertRaises(SystemExit):call_command('check_ledger',stdout=StringIO(),stderr=StringIO())
