from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client
from ..models import Approval, Expense, ExpenseRule, JournalEntry, JournalLine
from .. import services
from .base import RentalsTestCase

class ExpenseRuleTests(RentalsTestCase):
    def data(self,**kw):
        d={'period_id':self.period.pk,'category':'fuel','amount':Decimal('1000'),'paid_by':'operator','responsible':'owner','offset_owner':False,'reference':'REF-1','event_date':date(2026,9,5)}
        d.update(kw);return d

    def rec(self,key='k',**kw):return services.record_expense(self.org,self.user,self.data(**kw),key,can_override=kw.pop('can_override',False))

    def version(self):return self.agreement.versions.get(number=1)

    def test_default_categories_seeded(self):
        services.ensure_categories(self.org)
        self.assertEqual(self.org.expense_categories.count(),9)
        with self.assertRaises(services.DomainError):self.rec(category='bribe')

    def october(self,rules=(),changes=None):
        """Revise from October, put rules on the new (unused) version, issue October under it."""
        v2=services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),changes or {},'rules')
        for category,kw in rules:services.set_expense_rule(self.org,self.user,v2.pk,category,**kw)
        return v2,services.issue_period(self.org,self.agreement.pk,date(2026,10,1))

    def test_rule_resolved_from_period_version_and_snapshot_is_real(self):
        v2,p=self.october([('toll',{'responsible':'customer','approval_threshold':'200'})])
        d=self.data(period_id=p.pk,category='toll',responsible='customer',amount=Decimal('300'),event_date=date(2026,10,4))
        e=services.record_expense(self.org,self.user,d,'t1')
        rule=ExpenseRule.objects.get(agreement_version=v2,category='toll')
        self.assertEqual((e.rule_id,e.state),(rule.pk,'pending'))          # 300 >= rule threshold 200
        self.assertEqual(e.rule_snapshot['source'],'rule');self.assertEqual(e.rule_snapshot['approval_threshold'],'200.00');self.assertEqual(e.rule_snapshot['agreement_version'],2)
        a=Approval.objects.get(subject_id=str(e.pk));self.assertEqual((a.decision,a.threshold_at_time),('pending',Decimal('200.00')))
        with self.assertRaises(services.DomainError):services.record_expense(self.org,self.user,{**d,'responsible':'owner','reference':'R2'},'t2')   # contradicts rule

    def test_rule_change_does_not_rewrite_history(self):
        # period already issued under v1: its rules are frozen
        with self.assertRaises(services.DomainError):services.set_expense_rule(self.org,self.user,self.version().pk,'toll',responsible='customer')
        v2=services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{},'Rule change')
        services.set_expense_rule(self.org,self.user,v2.pk,'toll',responsible='customer')
        e=self.rec(category='toll',responsible='owner',amount=Decimal('50'))      # September (v1) has no toll rule: unchanged
        self.assertEqual(e.rule_snapshot['source'],'none')
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        with self.assertRaises(services.DomainError):services.record_expense(self.org,self.user,self.data(period_id=october.pk,category='toll',responsible='owner',reference='O1',event_date=date(2026,10,3)),'o1')

    def test_revision_copies_rules_forward(self):
        v2,_=self.october([('damage',{'responsible':'customer','proof_required':True})])
        v3=services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,11,1),{'owner_rent':'61000'},'r3')
        self.assertEqual(list(ExpenseRule.objects.filter(agreement_version=v3).values_list('category','proof_required')),[('damage',True)])

    def test_override_needs_permission_and_reason_and_goes_to_approval(self):
        with self.assertRaises(services.DomainError):self.rec('a',category='repair',responsible='operator',override_reason='')                   # no reason
        with self.assertRaises(services.DomainError):services.record_expense(self.org,self.user,self.data(category='repair',responsible='operator',override_reason='Goodwill',reference='x'),'b',can_override=False)
        e=services.record_expense(self.org,self.user,self.data(category='repair',responsible='operator',override_reason='Goodwill to customer',reference='y',amount=Decimal('100')),'c',can_override=True)
        self.assertEqual((e.state,e.overridden_by,e.override_reason),('pending',self.user,'Goodwill to customer'))   # small, but overrides always need approval
        services.approve_expense(self.org,self.user,e.pk);e.refresh_from_db();self.assertEqual(e.state,'posted')

    def test_threshold_approval_default_unchanged(self):
        self.assertEqual(self.rec('s',amount=Decimal('4999.99'),reference='S1').state,'posted')
        e=self.rec('l',amount=Decimal('5000'),reference='L1');self.assertEqual(e.state,'pending')
        services.approve_expense(self.org,self.user,e.pk)
        a=Approval.objects.get(subject_id=str(e.pk));self.assertEqual((a.decision,a.decided_by),('approved',self.user));self.assertIsNotNone(a.decided_at)

    def test_duplicate_warning_and_explicit_reason(self):
        first=self.rec('d1',reference='D1')
        with self.assertRaises(services.DomainError) as ctx:self.rec('d2',reference='D2')            # same agreement, amount, date, category
        self.assertIn(first.public_id,str(ctx.exception))
        second=self.rec('d3',reference='D3',allow_duplicate_reason='Two separate fill-ups')
        self.assertEqual(second.duplicate_reason,'Two separate fill-ups')
        self.assertEqual(self.rec('d4',reference='D4',amount=Decimal('1001')).state,'posted')       # different amount: no warning

    def test_proof_required_enforced_on_post_not_on_draft(self):
        v2,oct_p=self.october([('repair',{'responsible':'owner','proof_required':True})])
        base=self.data(period_id=oct_p.pk,category='repair',event_date=date(2026,10,2),reference='P1')
        with self.assertRaises(services.DomainError):services.record_expense(self.org,self.user,base,'p1')
        draft=services.record_expense(self.org,self.user,{**base,'draft':True},'p2')
        with self.assertRaises(services.DomainError):services.submit_expense(self.org,self.user,draft.pk)
        services.update_draft(self.org,self.user,draft.pk,{'receipt_reference':'BILL-77'})
        self.assertEqual(services.submit_expense(self.org,self.user,draft.pk).state,'posted')

    def test_draft_state_machine(self):
        d=services.record_expense(self.org,self.user,{**self.data(reference='DR1'),'draft':True},'dr')
        self.assertEqual(d.state,'draft');self.assertFalse(JournalEntry.objects.filter(source_type='expense',source_id=str(d.pk)).exists())
        self.assertEqual(services.statement(self.period)['owner']['separate_recoverable'],Decimal('0.00'))
        with self.assertRaises(services.DomainError):services.approve_expense(self.org,self.user,d.pk)      # cannot skip submit
        with self.assertRaises(services.DomainError):services.close_period(self.org,self.user,self.period.pk)  # draft blocks close
        services.update_draft(self.org,self.user,d.pk,{'amount':'2500'})
        p=services.submit_expense(self.org,self.user,d.pk);self.assertEqual((p.state,p.amount),('posted',Decimal('2500.00')))
        with self.assertRaises(services.DomainError):services.update_draft(self.org,self.user,d.pk,{'amount':'1'})      # posted: immutable
        with self.assertRaises(services.DomainError):services.discard_draft(self.org,self.user,d.pk)
        d2=services.record_expense(self.org,self.user,{**self.data(reference='DR2',amount=Decimal('77')),'draft':True},'dr2');services.discard_draft(self.org,self.user,d2.pk)
        self.assertFalse(Expense.objects.filter(pk=d2.pk).exists())

    def test_reversal_keeps_original_and_journal_nets_to_zero(self):
        e=self.rec('r',amount=Decimal('3000'),reference='RV')
        services.reverse_expense(self.org,self.user,e.pk,'Wrong vehicle')
        e.refresh_from_db();self.assertEqual((e.amount,e.state,e.reversal_reason),(Decimal('3000.00'),'reversed','Wrong vehicle'))
        net={}
        for l in JournalLine.objects.filter(entry__source_type__in=['expense','expense_reversal'],entry__source_id=str(e.pk)):net[l.account]=net.get(l.account,0)+l.debit-l.credit
        self.assertTrue(net and all(v==0 for v in net.values()),net)
        self.assertEqual(JournalEntry.objects.filter(source_id=str(e.pk),source_type__startswith='expense').count(),2)

    def test_owner_offset_blocked_when_rule_not_deductible(self):
        v2,oct_p=self.october([('repair',{'responsible':'owner','deductible_from':'none'})])
        with self.assertRaises(services.DomainError):services.record_expense(self.org,self.user,self.data(period_id=oct_p.pk,category='repair',offset_owner=True,reference='OF',event_date=date(2026,10,2)),'of')
        self.assertEqual(self.rec('ok',category='repair',offset_owner=True,reference='OF2',amount=Decimal('800')).state,'posted')   # September rule: offset allowed

    def test_worked_example_unchanged(self):
        e=self.rec('w',category='repair',amount=Decimal('8000'),offset_owner=True,reference='GAR-0182');self.assertEqual(e.state,'pending');services.approve_expense(self.org,self.user,e.pk)
        self.pay('customer_payment','70000','C');self.pay('owner_payout','40000','O');self.pay('driver_payout','15000','D')
        s=services.statement(self.period);self.assertEqual((s['customer']['due'],s['owner']['due'],s['driver']['due'],s['operator']['accrual_margin'],s['operator']['recorded_cash_movement']),(Decimal('30000'),Decimal('12000'),Decimal('5000'),Decimal('20000'),Decimal('7000')))

class ExpenseRuleApiTests(RentalsTestCase):
    def test_api_draft_submit_override_and_permissions(self):
        c=Client();c.force_login(self.user);base=f'/api/v1/orgs/{self.org.pk}'
        body={'period_id':self.period.pk,'category':'fuel','amount':'900','event_date':'2026-09-06','paid_by':'operator','responsible':'owner','reference':'API-1','draft':True}
        r=c.post(f'{base}/expenses',body,content_type='application/json',HTTP_IDEMPOTENCY_KEY='api1');self.assertEqual(r.status_code,200,r.content);eid=r.json()['id'];self.assertEqual(r.json()['state'],'draft')
        r=c.patch(f'{base}/expenses/{eid}',{'receipt_reference':'R-9'},content_type='application/json');self.assertEqual(r.json()['receipt_reference'],'R-9')
        self.assertEqual(c.post(f'{base}/expenses/{eid}/submit').json()['state'],'posted')
        bad=c.post(f'{base}/expenses',{**body,'draft':False,'reference':'API-2','amount':'10','responsible':'customer'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='api2');self.assertEqual(bad.status_code,422)
        ok=c.post(f'{base}/expenses',{**body,'draft':False,'reference':'API-3','amount':'11','responsible':'customer','override_reason':'Customer agreed'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='api3');self.assertEqual((ok.status_code,ok.json()['state']),(200,'pending'))
        ops=get_user_model().objects.create_user('ops',password='x')
        from ..models import Membership;Membership.objects.create(organization=self.org,user=ops,role='operations')
        oc=Client();oc.force_login(ops)
        denied=oc.post(f'{base}/expenses',{**body,'draft':False,'reference':'API-4','amount':'12','responsible':'customer','override_reason':'x'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='api4');self.assertEqual(denied.status_code,422)
        self.assertEqual(oc.put(f'{base}/agreement-versions/{self.agreement.versions.get().pk}/rules/toll',{'responsible':'customer'},content_type='application/json').status_code,403)
        self.assertEqual(oc.post(f'{base}/expense-categories',{'code':'x','name':'X'},content_type='application/json').status_code,403)
        cats=c.get(f'{base}/expense-categories').json();self.assertEqual(len(cats),9)
