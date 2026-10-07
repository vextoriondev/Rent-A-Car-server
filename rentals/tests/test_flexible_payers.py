"""M3b: any party may pay and any party may be responsible; the driver can be supplied and paid by operator, owner or customer."""
from datetime import date
from decimal import Decimal
from .. import services
from ..models import Agreement, Charge, Party, Vehicle
from ..services.helpers import DomainError
from .base import RentalsTestCase

D=Decimal

class PayerBase(RentalsTestCase):
    n=0
    def spend(self,paid_by,responsible,amount,category='other',offset=False):
        PayerBase.n+=1
        e=services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':category,'amount':D(amount),'event_date':date(2026,9,5),'paid_by':paid_by,'responsible':responsible,'offset_owner':offset,'reference':f'M3B-{self.n}','description':f'{paid_by} pays {responsible}'},f'm3b-{self.n}',can_override=True)
        self.assertEqual(e.state,'posted')
        return e

    def stmt(self):return services.statement(self.period)

    def assertReconciles(self):
        self.assertEqual(services.diff_statements(services.ledger_statement(self.period),services.obligation_statement(self.period)),[])

class ThirdPartyPayerTests(PayerBase):
    def test_a_party_paying_its_own_cost_is_a_memo_only(self):
        before=self.stmt()
        self.spend('customer','customer','3000',category='toll')       # customer pays their own bill
        self.spend('owner','owner','2500',category='driver_salary')    # owner pays the driver he supplied
        self.spend('driver','driver','400')
        self.assertEqual(self.stmt(),before);self.assertReconciles()

    def test_owner_fronts_a_customer_cost(self):
        self.spend('owner','customer','3000',category='damage')
        s=self.stmt()
        self.assertEqual(s['owner']['gross'],D('63000'));self.assertEqual(s['owner']['due'],D('63000'))   # we now owe the owner the 3,000 as well
        self.assertEqual(s['customer']['separate_recoverable'],D('3000'))                               # and can bill it to the customer
        self.assertEqual(s['operator']['accrual_margin'],D('20000'))                                    # margin untouched
        self.assertReconciles()
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'customer','kind':'reimbursement','amount':D('3000'),'direction':'increase'},'bill-cust')
        s=self.stmt();self.assertEqual(s['customer']['due'],D('103000'));self.assertEqual(s['customer']['separate_recoverable'],D('0'));self.assertReconciles()

    def test_customer_fronts_a_cost_the_operator_bears(self):
        self.spend('customer','operator','2000',category='cleaning')
        s=self.stmt()
        self.assertEqual(s['customer']['due'],D('98000'));self.assertEqual(s['operator']['operator_borne_costs'],D('2000'));self.assertEqual(s['operator']['accrual_margin'],D('18000'))
        self.assertReconciles()

    def test_customer_fronts_an_owner_repair_and_it_is_deducted_from_the_owner(self):
        self.spend('customer','owner','4000',category='repair')
        s=self.stmt()
        self.assertEqual(s['customer']['due'],D('96000'));self.assertEqual(s['owner']['separate_recoverable'],D('4000'))
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'owner','kind':'reimbursement','amount':D('4000'),'direction':'decrease'},'ded-owner')
        s=self.stmt();self.assertEqual(s['owner']['due'],D('56000'));self.assertEqual(s['owner']['separate_recoverable'],D('0'));self.assertReconciles()

    def test_driver_fronts_a_cost_and_is_owed_it(self):
        self.spend('driver','operator','500',category='toll')
        s=self.stmt();self.assertEqual(s['driver']['due'],D('20500'));self.assertEqual(s['operator']['accrual_margin'],D('19500'));self.assertReconciles()

    def test_operator_pays_a_driver_cost_and_recovers_it_from_salary(self):
        self.spend('operator','driver','1000',category='damage')
        s=self.stmt();self.assertEqual(s['driver']['separate_recoverable'],D('1000'));self.assertEqual(s['operator']['recorded_cash_movement'],D('-1000'));self.assertReconciles()
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'driver','kind':'reimbursement','amount':D('1000'),'direction':'decrease'},'ded-driver')
        s=self.stmt();self.assertEqual(s['driver']['due'],D('19000'));self.assertEqual(s['driver']['separate_recoverable'],D('0'));self.assertReconciles()

    def test_reversal_restores_every_side(self):
        before=self.stmt()
        for p,r in [('owner','customer'),('customer','operator'),('driver','operator'),('operator','driver'),('customer','owner')]:
            e=self.spend(p,r,'700')
            services.reverse_expense(self.org,self.user,e.pk,'entered on the wrong car')
        after=self.stmt()
        for side in ('customer','owner','driver','operator'):
            for k in ('due','separate_recoverable','accrual_margin','operator_borne_costs'):
                if k in before[side]:self.assertEqual(after[side][k],before[side][k],(side,k))
        self.assertReconciles()
        self.assertEqual(Charge.objects.filter(kind='expense_front').count(),8)  # 4 fronts + 4 mirrored reversals

    def test_owner_offset_still_needs_operator_funding(self):
        with self.assertRaises(DomainError):self.spend('owner','owner','1000',offset=True)

class FlexibleDriverTests(PayerBase):
    def agreement_with(self,reg,**terms):
        car=Vehicle.objects.create(organization=self.org,registration=reg,model_name='Honda Fit',owner=self.owner)
        a=Agreement(organization=self.org,vehicle=car,customer=self.customer,driver=self.driver,start_date=date(2026,1,1),customer_rent=D('90000'),owner_rent=D('55000'),driver_salary=D('0'),**terms)
        a,_=services.create_agreement(self.org,self.user,a)
        return a

    def test_owner_supplied_and_paid_driver_creates_no_salary_payable(self):
        a=self.agreement_with('DHA-20-0001',driver_provider='owner',driver_paid_by='owner',clauses='Owner supplies and pays the driver.')
        p=services.issue_period(self.org,a.pk,date(2026,9,1))
        s=services.statement(p)
        self.assertEqual((s['driver']['earned'],s['driver']['due']),(D('0'),D('0')))
        self.assertEqual(s['operator']['accrual_margin'],D('35000'))
        self.assertEqual(p.obligations.get(kind='driver_salary').amount,D('0'))
        self.assertEqual(services.diff_statements(services.ledger_statement(p),services.obligation_statement(p)),[])

    def test_customers_own_driver(self):
        a=self.agreement_with('DHA-20-0002',driver_provider='customer',driver_paid_by='customer')
        p=services.issue_period(self.org,a.pk,date(2026,9,1))
        self.assertEqual(services.statement(p)['driver']['due'],D('0'))

    def test_operator_paid_driver_still_needs_a_salary(self):
        car=Vehicle.objects.create(organization=self.org,registration='DHA-20-0003',model_name='Honda Fit',owner=self.owner)
        a=Agreement(organization=self.org,vehicle=car,customer=self.customer,driver=self.driver,start_date=date(2026,1,1),customer_rent=D('90000'),owner_rent=D('55000'),driver_salary=D('0'))
        with self.assertRaises(DomainError):services.create_agreement(self.org,self.user,a)

    def test_unknown_driver_roles_are_rejected(self):
        with self.assertRaises(DomainError):self.agreement_with('DHA-20-0004',driver_provider='mechanic')

    def test_revision_can_change_clauses_and_who_pays_the_driver(self):
        v=services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'driver_paid_by':'owner','driver_provider':'owner','driver_salary':'0','clauses':'From October the owner supplies the driver.'},'Owner takes over the driver')
        self.assertEqual((v.driver_provider,v.driver_paid_by,v.clauses),('owner','owner','From October the owner supplies the driver.'))
        self.agreement.refresh_from_db()
        p=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        self.assertEqual(services.statement(p)['driver']['earned'],D('0'))
        self.assertEqual(services.statement(self.period)['driver']['earned'],D('20000'))  # September stays as it was
        with self.assertRaises(DomainError):services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,11,1),{'driver_paid_by':'bank'},'bad')
