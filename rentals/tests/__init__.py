from datetime import date
from decimal import Decimal
import json
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test import Client
from .models import Organization, Membership, Party, Vehicle, Agreement, Obligation, Expense, JournalEntry, NotificationOutbox
from . import services

class LedgerFlowTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('accountant',password='test-only-password')
        self.org=Organization.objects.create(name='Sample Transport')
        Membership.objects.create(organization=self.org,user=self.user,role='accountant')
        self.owner=Party.objects.create(organization=self.org,kind='owner',name='Farhana Rahman')
        self.driver=Party.objects.create(organization=self.org,kind='driver',name='Imran Hossain')
        self.customer=Party.objects.create(organization=self.org,kind='customer',name='Meridian Textiles Ltd.')
        self.car=Vehicle.objects.create(organization=self.org,registration='DHA-19-7724',model_name='Toyota Axio',owner=self.owner)
        self.agreement=Agreement.objects.create(organization=self.org,vehicle=self.car,customer=self.customer,driver=self.driver,start_date=date(2026,1,1),customer_rent=Decimal('100000'),owner_rent=Decimal('60000'),driver_salary=Decimal('20000'),repair_responsible='owner',fuel_responsible='owner',owner_offset_allowed=True)
        self.period=services.issue_period(self.org,self.agreement.pk,date(2026,9,1))
        self.ob={x.kind:x for x in self.period.obligations.all()}

    def pay(self,kind,amount,reference):
        target={'customer_payment':'customer_rent','owner_payout':'owner_rent','driver_payout':'driver_salary'}[kind]
        return services.record_payment(self.org,self.user,{'kind':kind,'amount':Decimal(amount),'reference':reference,'method':'bank','event_date':date(2026,9,20),'allocations':[{'obligation_id':self.ob[target].pk,'amount':Decimal(amount)}]},reference)

    def test_worked_month_balances_and_journal(self):
        data={'period_id':self.period.pk,'category':'repair','amount':Decimal('8000'),'paid_by':'operator','responsible':'owner','offset_owner':True,'reference':'GAR-0182','description':'Brake repair'}
        expense=services.record_expense(self.org,self.user,data,'expense-1')
        self.assertEqual(expense.state,Expense.State.PENDING)
        self.assertEqual(services.statement(self.period)['owner']['due'],Decimal('60000'))
        services.approve_expense(self.org,self.user,expense.pk)
        self.pay('customer_payment','40000','CUST-1')
        self.pay('customer_payment','30000','CUST-2')
        self.pay('owner_payout','40000','OWNER-1')
        self.pay('driver_payout','15000','DRIVER-1')
        s=services.statement(self.period)
        self.assertEqual(s['customer']['due'],Decimal('30000'))
        self.assertEqual(s['owner']['offsets'],Decimal('8000'))
        self.assertEqual(s['owner']['due'],Decimal('12000'))
        self.assertEqual(s['driver']['due'],Decimal('5000'))
        self.assertEqual(s['operator']['accrual_margin'],Decimal('20000'))
        self.assertEqual(s['operator']['recorded_cash_movement'],Decimal('7000'))
        for entry in JournalEntry.objects.all():
            self.assertEqual(sum((x.debit for x in entry.lines.all()),Decimal('0')),sum((x.credit for x in entry.lines.all()),Decimal('0')))

    def test_partial_payment_idempotency_and_overpayment(self):
        p=self.pay('customer_payment','25000','PAY-1')
        again=self.pay('customer_payment','25000','PAY-1')
        self.assertEqual(p.pk,again.pk)
        with self.assertRaises(services.DomainError):self.pay('customer_payment','26000','PAY-1')
        with self.assertRaises(services.DomainError):self.pay('customer_payment','80000','PAY-2')
        self.assertEqual(services.statement(self.period)['customer']['due'],Decimal('75000'))

    def test_approval_and_close_gate(self):
        data={'period_id':self.period.pk,'category':'repair','amount':Decimal('8000'),'paid_by':'operator','responsible':'owner','offset_owner':True,'reference':'GAR-0199'}
        e=services.record_expense(self.org,self.user,data,'EXP-2')
        with self.assertRaises(services.DomainError):services.close_period(self.org,self.user,self.period.pk)
        services.approve_expense(self.org,self.user,e.pk)
        closed=services.close_period(self.org,self.user,self.period.pk)
        self.assertIsNotNone(closed.closed_at)
        self.assertEqual(services.close_period(self.org,self.user,self.period.pk).pk,closed.pk)
        with self.assertRaises(services.DomainError):self.pay('customer_payment','1000','LATE-1')
        self.assertEqual(NotificationOutbox.objects.filter(event_type='settlement_closed').count(),3)

    def test_cross_organization_is_hidden(self):
        second=Organization.objects.create(name='Other fleet')
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/api/v1/orgs/{self.org.pk}/agreements').status_code,200)
        self.assertEqual(self.client.get(f'/api/v1/orgs/{second.pk}/agreements').status_code,404)

    def test_http_payment_and_snapshot(self):
        self.client=Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        csrf_response=self.client.get('/api/v1/csrf')
        self.assertEqual(csrf_response.status_code,204)
        token=csrf_response.cookies['csrftoken'].value
        payload={'kind':'customer_payment','amount':'10000.00','reference':'API-RECEIPT-1','method':'bank','event_date':'2026-09-20','allocations':[{'obligation_id':self.ob['customer_rent'].pk,'amount':'10000.00'}]}
        url=f'/api/v1/orgs/{self.org.pk}/payments'
        response=self.client.post(url,data=json.dumps(payload),content_type='application/json',HTTP_IDEMPOTENCY_KEY='api-receipt-1',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,200,response.content)
        snapshot=self.client.get(f'/api/v1/orgs/{self.org.pk}/snapshot?month=2026-09-01')
        self.assertEqual(snapshot.status_code,200,snapshot.content)
        body=snapshot.json()
        self.assertEqual(body['agreements'][0]['customer'],'Meridian Textiles Ltd.')
        self.assertEqual(body['entries'][0]['amount'],10000)
        self.assertEqual(body['backend']['obligationIds'][f'AGR-{self.agreement.pk}']['customer'],self.ob['customer_rent'].pk)
