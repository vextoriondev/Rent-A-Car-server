"""M16a-2: overtime, absence deduction and fine charges on the existing charges engine."""
from datetime import date
from decimal import Decimal
from ..models import AuditEvent
from .. import services
from .test_reports import ReportBase

D=Decimal

class ExtraChargeTests(ReportBase):
    def post(self,key,side,kind,can_approve=False,description='because',**kw):
        data={'period_id':self.period.pk,'side':side,'kind':kind,'description':description,**kw}
        return services.post_charge(self.org,self.user,data,key,can_approve=can_approve)

    def st(self):return services.statement(self.period)

    def reconciles(self):
        self.assertEqual(services.diff_statements(services.ledger_statement(self.period),services.obligation_statement(self.period)),[])

    def test_overtime_increases_what_the_customer_owes_and_the_margin(self):
        before=self.st()
        c=self.post('ot-1','customer','overtime',quantity=D(4),unit_rate=D(250))
        self.assertEqual((c.amount,c.direction,c.quantity,c.unit_rate),(D(1000),'increase',D(4),D(250)))
        s=self.st();self.assertEqual(s['customer']['due']-before['customer']['due'],D(1000))
        self.assertEqual(s['operator']['accrual_margin']-before['operator']['accrual_margin'],D(1000))
        self.reconciles()

    def test_absence_deduction_reduces_the_driver_salary_due_and_costs_less(self):
        before=self.st()
        c=self.post('ab-1','driver','absence_deduction',quantity=D(2),unit_rate=D(700))
        self.assertEqual((c.amount,c.direction),(D(1400),'decrease'))
        s=self.st();self.assertEqual(before['driver']['due']-s['driver']['due'],D(1400))
        self.assertEqual(s['operator']['accrual_margin']-before['operator']['accrual_margin'],D(1400))
        self.reconciles()

    def test_fine_on_customer_increases_and_on_driver_decreases(self):
        before=self.st()
        self.post('f-1','customer','fine',amount=D(500),description='parking fine')
        self.post('f-2','driver','fine',amount=D(300),description='traffic fine')
        s=self.st();self.assertEqual(s['customer']['due']-before['customer']['due'],D(500));self.assertEqual(before['driver']['due']-s['driver']['due'],D(300))
        self.reconciles()
        with self.assertRaisesMessage(services.DomainError,'customer or the driver'):self.post('f-3','owner','fine',amount=D(1))

    def test_wrong_sides_and_missing_reason_or_amount_are_refused(self):
        with self.assertRaisesMessage(services.DomainError,'customer'):self.post('x1','driver','overtime',amount=D(100))
        with self.assertRaisesMessage(services.DomainError,'driver'):self.post('x2','customer','absence_deduction',amount=D(100))
        with self.assertRaisesMessage(services.DomainError,'reason'):self.post('x3','customer','fine',amount=D(100),description='  ')
        with self.assertRaisesMessage(services.DomainError,'amount'):self.post('x4','customer','overtime')
        with self.assertRaisesMessage(services.DomainError,'above zero'):self.post('x5','customer','overtime',quantity=D(0),unit_rate=D(10))

    def test_amount_must_equal_quantity_times_rate_when_both_are_given(self):
        with self.assertRaisesMessage(services.DomainError,'quantity'):self.post('m1','customer','overtime',quantity=D(2),unit_rate=D(100),amount=D(999))
        self.assertEqual(self.post('m2','customer','overtime',quantity=D(2),unit_rate=D(100),amount=D(200)).amount,D(200))

    def test_idempotent_and_large_amounts_need_an_approver(self):
        a=self.post('i-1','customer','fine',amount=D(500));b=self.post('i-1','customer','fine',amount=D(500));self.assertEqual(a.pk,b.pk)
        with self.assertRaisesMessage(services.DomainError,'approval'):self.post('big','customer','fine',amount=D(5000))   # default threshold 5,000
        self.assertEqual(self.post('big','customer','fine',amount=D(5000),can_approve=True).amount,D(5000))

    def test_audited_with_quantity_and_rate(self):
        c=self.post('au-1','customer','overtime',quantity=D(3),unit_rate=D(100))
        e=AuditEvent.objects.filter(action='charge.post',target_id=str(c.pk)).first();self.assertIsNotNone(e)
        self.assertEqual(str(e.after['kind']),'overtime');self.assertEqual(D(str(e.after['quantity'])),D(3));self.assertEqual(D(str(e.after['unit_rate'])),D(100))

    def test_suggestions_use_the_agreement_rates(self):
        services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'overtime_rate':D(300),'overtime_unit':'hour'},'overtime terms')
        p2=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        s=services.charge_suggestion(p2,'overtime',D(5));self.assertEqual((s['unit_rate'],s['amount'],s['unit']),(D(300),D(1500),'hour'))
        s=services.charge_suggestion(p2,'absence_deduction',D(2));self.assertEqual(s['unit_rate'],(D(20000)/31).quantize(D('0.01')));self.assertEqual(s['unit'],'day')
        self.assertIsNone(services.charge_suggestion(self.period,'overtime',D(1))['unit_rate'])      # September has no overtime rate

    def test_api_posts_with_quantity_and_rate_and_serves_suggestions(self):
        r=self.c.post(f'{self.base}/charges',data={'period_id':self.period.pk,'side':'customer','kind':'overtime','quantity':'4','unit_rate':'250','description':'late return'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='api-ot')
        self.assertEqual(r.status_code,200,r.content);j=r.json();self.assertEqual((j['kind'],j['amount'],j['quantity'],j['unit_rate']),('overtime','1000.00','4.00','250.00'))
        s=self.c.get(f'{self.base}/periods/{self.period.pk}/charge-suggestion?kind=overtime&quantity=2');self.assertEqual(s.status_code,200,s.content);self.assertIsNone(s.json()['unit_rate'])

    def test_api_large_amount_needs_an_administrator(self):
        body={'period_id':self.period.pk,'side':'customer','kind':'fine','amount':'6000','description':'damage fine'}
        no=self.c.post(f'{self.base}/charges',data=body,content_type='application/json',HTTP_IDEMPOTENCY_KEY='api-big');self.assertEqual(no.status_code,422,no.content)   # the fixture user is an accountant
        from django.contrib.auth import get_user_model
        from django.test import Client
        from ..models import Membership
        admin=get_user_model().objects.create_user('boss',password='x');Membership.objects.create(organization=self.org,user=admin,role='admin')
        c=Client();c.force_login(admin)
        yes=c.post(f'{self.base}/charges',data=body,content_type='application/json',HTTP_IDEMPOTENCY_KEY='api-big2');self.assertEqual(yes.status_code,200,yes.content)

    def test_a_proof_file_can_be_attached_to_a_fine(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        c=self.post('pf-1','customer','fine',amount=D(500),description='parking ticket')
        r=self.c.post(f'{self.base}/documents',data={'target_type':'charge','target_id':c.pk,'file':SimpleUploadedFile('ticket.pdf',b'%PDF-1.4 test',content_type='application/pdf')})
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(len(self.c.get(f'{self.base}/documents?target_type=charge&target_id={c.pk}').json()),1)

    def test_worked_example_unchanged(self):
        s=self.st();self.assertEqual((s['customer']['due'],s['owner']['due'],s['driver']['due'],s['operator']['accrual_margin']),(D(30000),D(12000),D(5000),D(20000)))
