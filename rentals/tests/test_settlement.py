import threading
import unittest
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase
from ..models import Allocation, Charge, Expense, Membership, NotificationOutbox, Obligation, Period, Settlement
from .. import services
from .base import RentalsTestCase, build_worked_fixture

D=Decimal

class SettlementBase(RentalsTestCase):
    def setUp(self):
        super().setUp()
        self.admin=get_user_model().objects.create_user('boss',password='x');Membership.objects.create(organization=self.org,user=self.admin,role='admin')

    def settle(self,period=None):
        """prepare -> request -> approve (admin) -> close; returns the closed settlement."""
        p=period or self.period
        s=services.prepare_settlement(self.org,self.user,p.pk)
        services.request_settlement_approval(self.org,self.user,s.pk)
        services.approve_settlement(self.org,self.admin,s.pk,'admin')
        return services.close_settlement(self.org,self.user,s.pk)

    def pay_all_but(self):
        self.pay('customer_payment','70000','S-C');self.pay('owner_payout','40000','S-O');self.pay('driver_payout','15000','S-D')

    def codes(self,period=None,severity=None):
        return {e['code'] for e in services.preview_settlement(self.org,(period or self.period).pk)['exceptions'] if severity in(None,e['severity'])}

class ReadinessTests(SettlementBase):
    def test_preview_structures_exceptions_and_ready_flag(self):
        pre=services.preview_settlement(self.org,self.period.pk);self.assertTrue(pre['ready']);self.assertIn('uninvoiced_obligation',self.codes())
        services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'repair','amount':D(6000),'paid_by':'operator','responsible':'owner','offset_owner':False,'reference':'BIG','event_date':date(2026,9,3)},'big')
        pre=services.preview_settlement(self.org,self.period.pk);self.assertFalse(pre['ready'])
        e=next(x for x in pre['exceptions'] if x['code']=='pending_expense');self.assertEqual((e['severity'],e['message']),('blocking','Pending expense approval blocks close'));self.assertTrue(e['ref'])
        services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'fuel','amount':D(100),'paid_by':'operator','responsible':'owner','reference':'DR','event_date':date(2026,9,4),'draft':True},'dr')
        self.assertIn('draft_expense',self.codes(severity='blocking'))

    def test_owner_owes_operator_warns_by_default_and_blocks_when_configured(self):
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'owner','kind':'adjustment','direction':'decrease','amount':D(70000)},'neg')
        self.assertIn('owner_owes_operator',self.codes(severity='warning'));self.assertTrue(services.preview_settlement(self.org,self.period.pk)['ready'])
        services.update_settings(self.org,self.admin,negative_owner_blocks_close=True)
        self.assertFalse(services.preview_settlement(self.org,self.period.pk)['ready']);self.assertIn('owner_owes_operator',self.codes(severity='blocking'))

    def test_prior_period_warnings(self):
        self.pay_all_but();october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        self.assertIn('prior_period_open',self.codes(october))
        self.settle();self.assertIn('prior_period_balance',self.codes(october))
        services.carry_forward(self.org,self.user,october.pk);self.assertNotIn('prior_period_balance',self.codes(october))

class WorkflowTests(SettlementBase):
    def test_full_flow_freezes_lines_that_sum_to_dues_and_closes_cleanly(self):
        e=services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'repair','amount':D(8000),'paid_by':'operator','responsible':'owner','offset_owner':True,'reference':'GAR-0182','event_date':date(2026,9,3)},'g')
        services.approve_expense(self.org,self.user,e.pk);self.pay_all_but()
        before=services.statement(self.period)
        s=services.prepare_settlement(self.org,self.user,self.period.pk);self.assertEqual(s.state,'draft')
        self.assertEqual(services.prepare_settlement(self.org,self.user,self.period.pk).pk,s.pk)
        s=services.request_settlement_approval(self.org,self.user,s.pk);self.assertEqual(s.state,'pending_approval')
        s=services.approve_settlement(self.org,self.admin,s.pk,'admin');self.assertEqual((s.state,s.approved_by),('approved',self.admin))
        s=services.close_settlement(self.org,self.user,s.pk)
        self.period.refresh_from_db();self.assertIsNotNone(self.period.closed_at);self.assertEqual(s.state,'closed')
        self.assertEqual(services.statement(self.period)['customer']['due'],before['customer']['due'])
        due={'customer':D(30000),'owner':D(12000),'driver':D(5000)}
        for side,amount in due.items():
            rows=s.lines.filter(section=side).exclude(label='Due');self.assertEqual(sum(r.amount for r in rows),amount,side)
            self.assertEqual(s.lines.get(section=side,label='Due').amount,amount)
        self.assertTrue(s.lines.filter(source_type='payment').exists());self.assertTrue(s.lines.filter(source_type='expense',section='owner').exists())
        self.assertEqual(s.lines.get(label='Accrual margin').amount,D(20000))
        self.assertEqual(NotificationOutbox.objects.filter(event_type='settlement_closed').count(),3)

    def test_blocking_exception_stops_request_and_state_order_is_enforced(self):
        s=services.prepare_settlement(self.org,self.user,self.period.pk)
        with self.assertRaises(services.DomainError):services.approve_settlement(self.org,self.admin,s.pk,'admin')       # not requested
        with self.assertRaises(services.DomainError):services.close_settlement(self.org,self.user,s.pk)                   # not approved
        services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'repair','amount':D(6000),'paid_by':'operator','responsible':'owner','reference':'P','event_date':date(2026,9,3)},'p')
        with self.assertRaises(services.DomainError) as ctx:services.request_settlement_approval(self.org,self.user,s.pk)
        self.assertIn('Pending expense approval',str(ctx.exception))

    def test_approval_authority_comes_from_org_settings(self):
        s=services.prepare_settlement(self.org,self.user,self.period.pk);services.request_settlement_approval(self.org,self.user,s.pk)
        with self.assertRaises(services.DomainError):services.approve_settlement(self.org,self.user,s.pk,'accountant')
        services.update_settings(self.org,self.admin,approval_role='accountant')
        self.assertEqual(services.approve_settlement(self.org,self.user,s.pk,'accountant').state,'approved')
        with self.assertRaises(services.DomainError):services.update_settings(self.org,self.admin,approval_role='viewer')

    def test_statement_change_after_approval_blocks_close(self):
        s=services.prepare_settlement(self.org,self.user,self.period.pk);services.request_settlement_approval(self.org,self.user,s.pk);services.approve_settlement(self.org,self.admin,s.pk,'admin')
        self.pay('customer_payment','1000','LATE-1')
        with self.assertRaises(services.DomainError) as ctx:services.close_settlement(self.org,self.user,s.pk)
        self.assertIn('changed since approval',str(ctx.exception))
        s=services.request_settlement_approval(self.org,self.user,s.pk);services.approve_settlement(self.org,self.admin,s.pk,'admin');self.assertEqual(services.close_settlement(self.org,self.user,s.pk).state,'closed')

    def test_close_retry_is_idempotent(self):
        self.pay_all_but();s=self.settle()
        obligations=Obligation.objects.count();notices=NotificationOutbox.objects.count();charges=Charge.objects.count()
        again=services.close_settlement(self.org,self.user,s.pk);self.assertEqual((again.pk,again.state),(s.pk,'closed'))
        services.close_period(self.org,self.user,self.period.pk);services.close_period(self.org,self.user,self.period.pk)
        self.assertEqual((Obligation.objects.count(),NotificationOutbox.objects.count(),Charge.objects.count(),Settlement.objects.count()),(obligations,notices,charges,1))
        self.assertEqual(NotificationOutbox.objects.filter(event_type='settlement_closed').count(),3)

    def test_wrapper_close_period_keeps_legacy_messages_and_creates_settlement(self):
        services.record_expense(self.org,self.user,{'period_id':self.period.pk,'category':'repair','amount':D(6000),'paid_by':'operator','responsible':'owner','reference':'W','event_date':date(2026,9,3)},'w')
        with self.assertRaises(services.DomainError) as ctx:services.close_period(self.org,self.user,self.period.pk)
        self.assertEqual(str(ctx.exception),'Pending expense approval blocks close');self.assertFalse(Settlement.objects.filter(state='closed').exists())
        Expense.objects.filter(reference='W').update(state='posted')
        p=services.close_period(self.org,self.user,self.period.pk);self.assertIsNotNone(p.closed_at);self.assertEqual(Settlement.objects.get().state,'closed')

class ReopenTests(SettlementBase):
    def test_reopen_needs_reason_then_period_accepts_entries_and_recloses_with_new_notices(self):
        self.pay_all_but();s=self.settle()
        with self.assertRaises(services.DomainError):services.reopen_settlement(self.org,self.admin,s.pk,'  ')
        s=services.reopen_settlement(self.org,self.admin,s.pk,'Missed fuel bill');self.assertEqual((s.state,s.version,s.reopen_reason,s.reopened_by),('reopened',2,'Missed fuel bill',self.admin))
        self.period.refresh_from_db();self.assertIsNone(self.period.closed_at)
        self.pay('customer_payment','5000','POST-REOPEN')
        s=services.request_settlement_approval(self.org,self.user,s.pk);services.approve_settlement(self.org,self.admin,s.pk,'admin');s=services.close_settlement(self.org,self.user,s.pk)
        self.assertEqual((s.state,s.version),('closed',2));self.assertEqual(NotificationOutbox.objects.filter(event_type='settlement_closed').count(),6)
        self.assertEqual(Settlement.objects.count(),1)

    def test_only_latest_month_and_never_after_carry_forward(self):
        self.pay_all_but();sept=self.settle()
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        services.carry_forward(self.org,self.user,october.pk)
        with self.assertRaises(services.DomainError) as ctx:services.reopen_settlement(self.org,self.admin,sept.pk,'why')
        self.assertIn('carried forward',str(ctx.exception))
        oct_s=services.prepare_settlement(self.org,self.user,october.pk)
        self.assertEqual(oct_s.state,'draft')

    def test_cannot_reopen_earlier_month_when_later_is_closed(self):
        self.pay_all_but();sept=self.settle()
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        october.closed_at=sept.closed_at;october.save()
        services.prepare_settlement(self.org,self.user,october.pk)
        with self.assertRaises(services.DomainError) as ctx:services.reopen_settlement(self.org,self.admin,sept.pk,'x')
        self.assertIn('latest closed month',str(ctx.exception))

    def test_only_closed_settlements_can_be_reopened(self):
        s=services.prepare_settlement(self.org,self.user,self.period.pk)
        with self.assertRaises(services.DomainError):services.reopen_settlement(self.org,self.admin,s.pk,'x')

class LateEntryTests(SettlementBase):
    data=lambda self,**kw:{'period_id':self.period.pk,'category':'fuel','amount':D(900),'paid_by':'operator','responsible':'owner','offset_owner':False,'reference':'LATE-F','event_date':date(2026,9,29),**kw}

    def test_closed_period_never_takes_entry_silently(self):
        self.pay_all_but();self.settle()
        with self.assertRaises(services.DomainError) as ctx:services.record_expense(self.org,self.user,self.data(),'l1')
        self.assertIn('late_entry=true',str(ctx.exception));self.assertIn('2026-10',str(ctx.exception))
        with self.assertRaises(services.DomainError) as ctx:services.record_expense(self.org,self.user,self.data(late_entry=True),'l2')
        self.assertIn('Issue 2026-10 first',str(ctx.exception))

    def test_late_entry_posts_to_next_open_period_as_linked_adjustment(self):
        self.pay_all_but();self.settle();sept_before=services.statement(self.period)
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        e=services.record_expense(self.org,self.user,self.data(late_entry=True),'l3')
        self.assertEqual((e.period,e.adjusts_period,e.state),(october,self.period,'posted'));self.assertTrue(e.description.startswith('Late entry for 2026-09'))
        self.assertEqual(services.statement(self.period),sept_before)                                       # closed month untouched
        self.assertEqual(services.statement(october)['operator']['operator_borne_costs'],D(0));self.assertEqual(services.statement(october)['owner']['separate_recoverable'],D(900))

    def test_late_entry_refused_when_next_period_closed(self):
        self.pay_all_but();self.settle()
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1));from django.utils import timezone
        october.closed_at=timezone.now();october.save()
        with self.assertRaises(services.DomainError):services.record_expense(self.org,self.user,self.data(late_entry=True),'l4')

    def test_payment_into_closed_period_points_to_carry_forward(self):
        self.pay_all_but();self.settle()
        with self.assertRaises(services.DomainError) as ctx:self.pay('customer_payment','1000','CLOSED-P')
        self.assertIn('carry its balance forward',str(ctx.exception))

class SettlementApiTests(SettlementBase):
    def test_api_flow_permissions_and_wrapper(self):
        c=Client();c.force_login(self.user);a=Client();a.force_login(self.admin);base=f'/api/v1/orgs/{self.org.pk}'
        pre=c.get(f'{base}/periods/{self.period.pk}/settlement-preview').json();self.assertTrue(pre['ready']);self.assertIn('customer',pre['statement'])
        sid=c.post(f'{base}/periods/{self.period.pk}/settlement').json()['id']
        r=c.post(f'{base}/settlements/{sid}/request-approval');self.assertEqual((r.status_code,r.json()['state']),(200,'pending_approval'));self.assertTrue(r.json()['lines'])
        self.assertEqual(c.post(f'{base}/settlements/{sid}/approve').status_code,422)                       # accountant, approval_role=admin
        self.assertEqual(a.post(f'{base}/settlements/{sid}/approve').json()['state'],'approved')
        self.assertEqual(c.post(f'{base}/settlements/{sid}/close').json()['state'],'closed')
        self.assertEqual(c.post(f'{base}/settlements/{sid}/reopen',{'reason':'x'},content_type='application/json').status_code,403)
        self.assertEqual(a.post(f'{base}/settlements/{sid}/reopen',{'reason':'fix'},content_type='application/json').json()['state'],'reopened')
        self.assertEqual(c.put(f'{base}/settlement-settings',{'approval_role':'accountant'},content_type='application/json').status_code,403)
        self.assertEqual(a.put(f'{base}/settlement-settings',{'approval_role':'accountant'},content_type='application/json').json()['approval_role'],'accountant')
        self.assertEqual(c.get(f'{base}/settlements?month=2026-09-01').json()['items'][0]['state'],'reopened')
        self.assertEqual(c.get(f'{base}/settlements/{sid}').json()['version'],2)
        self.assertEqual(c.post(f'{base}/periods/{self.period.pk}/close').json()['closed'],True)         # one-button wrapper still works
        own=get_user_model().objects.create_user('own3',password='x');services.grant_party_access(self.org,self.owner.pk,'own3')
        oc=Client();oc.force_login(own)
        self.assertEqual(oc.get(f'{base}/settlements').status_code,403)

@unittest.skipUnless(connection.vendor=='postgresql','SQLite ignores select_for_update')
class ConcurrentCloseTests(TransactionTestCase):
    def setUp(self):
        f=build_worked_fixture();self.org=f['org'];self.user=f['user'];self.period=f['period']
        s=services.prepare_settlement(self.org,self.user,self.period.pk);services.request_settlement_approval(self.org,self.user,s.pk);services.approve_settlement(self.org,self.user,s.pk,'admin',shortcut=True);self.sid=s.pk
        connection.close()

    def test_two_concurrent_closes_close_once_and_notify_once(self):
        results=[]
        def attempt():
            try:results.append(services.close_settlement(self.org,self.user,self.sid).state)
            except Exception as exc:results.append(type(exc).__name__)
            finally:connection.close()
        ts=[threading.Thread(target=attempt) for _ in range(2)];[t.start() for t in ts];[t.join() for t in ts]
        self.assertEqual(results,['closed','closed'])
        self.assertEqual(NotificationOutbox.objects.filter(event_type='settlement_closed').count(),3)
        self.assertEqual(Settlement.objects.filter(state='closed').count(),1)

class SettlementBackfillMigrationTests(TransactionTestCase):
    def migrate(self,target):
        from django.db.migrations.executor import MigrationExecutor
        ex=MigrationExecutor(connection);ex.migrate([('rentals',target)]);return ex.loader.project_state([('rentals',target)]).apps
    def tearDown(self):
        ex=MigrationExecutor(connection);ex.migrate(ex.loader.graph.leaf_nodes())
    def test_closed_periods_get_a_closed_settlement(self):
        from django.utils import timezone
        apps=self.migrate('0012_settlement_engine');g=apps.get_model
        Org=g('rentals','Organization');Party=g('rentals','Party');Vehicle=g('rentals','Vehicle');Agreement=g('rentals','Agreement');Period=g('rentals','Period')
        org=Org.objects.create(name='L');o=Party.objects.create(organization=org,kind='owner',name='O');c=Party.objects.create(organization=org,kind='customer',name='C');d=Party.objects.create(organization=org,kind='driver',name='D')
        car=Vehicle.objects.create(organization=org,registration='M-1',model_name='X',owner=o)
        a=Agreement.objects.create(organization=org,vehicle=car,customer=c,driver=d,start_date=date(2026,1,1),customer_rent=D(1),owner_rent=D(1),driver_salary=D(1))
        closed=Period.objects.create(organization=org,agreement=a,month=date(2026,8,1),closed_at=timezone.now());Period.objects.create(organization=org,agreement=a,month=date(2026,9,1))
        apps=self.migrate('0013_backfill_settlements')
        S=apps.get_model('rentals','Settlement');self.assertEqual(S.objects.count(),1);s=S.objects.get();self.assertEqual((s.period_id,s.state,s.month),(closed.pk,'closed',date(2026,8,1)))
