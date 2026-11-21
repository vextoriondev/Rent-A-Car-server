"""Regression tests for bugs found by the end-to-end QA round (2026-11-22). Everything goes through the real HTTP API."""
import json
from decimal import Decimal as D
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from ..models import Organization, Membership, FinancialAccount

User = get_user_model()


class QaFixTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='QA')
        u = User.objects.create_user('qa_fix', password='x')
        Membership.objects.create(organization=self.org, user=u, role='admin', is_super_admin=True)
        self.c = Client()
        self.c.force_login(u)
        self.base = f'/api/v1/orgs/{self.org.pk}'
        self.cash = FinancialAccount.objects.create(organization=self.org, account_id='ACC-001', name='Cash', account_type='Cash', opening_balance=1000, current_balance=1000)
        self.bank = FinancialAccount.objects.create(organization=self.org, account_id='ACC-002', name='Bank', account_type='Bank', opening_balance=5000, current_balance=5000)
        r = self.post('/tasin-clients', {'name': 'Cl', 'contact_person': 'P', 'phone': '01700000000'})
        self.client_id = int(r.json()['id'])

    def post(self, path, body):
        return self.c.post(self.base + path, json.dumps(body), content_type='application/json')

    def invoice(self, **kw):
        body = {'client_id': self.client_id, 'issue_date': '2026-11-20', 'due_date': '2026-12-05', 'custom_items': [{'description': 'Job', 'quantity': 1, 'unit_price': 1000}]}
        body.update(kw)
        return self.post('/tasin-invoices', body)

    def test_discount_larger_than_invoice_is_rejected_not_clamped(self):
        r = self.invoice(discount=5000)
        self.assertEqual(r.status_code, 422, r.content)
        self.assertEqual(self.c.get(self.base + '/tasin-invoices').json(), [])           # nothing was created
        r = self.invoice(discount=-1)
        self.assertEqual(r.status_code, 422)
        ok = self.invoice(discount=1000)                                                  # a discount equal to the amount is allowed (free job)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(D(str(ok.json()['totalAmount'])), 0)

    def test_receipt_without_account_goes_to_the_account_of_that_payment_method(self):
        inv = self.invoice().json()
        r = self.post(f'/tasin-invoices/{inv["id"]}/payments', {'date': '2026-11-22', 'amount': 400, 'payment_method': 'Bank Transfer'})
        self.assertEqual(r.status_code, 200, r.content)
        self.cash.refresh_from_db(); self.bank.refresh_from_db()
        self.assertEqual((self.cash.current_balance, self.bank.current_balance), (D('1000'), D('5400')))
        r = self.post(f'/tasin-invoices/{inv["id"]}/payments', {'date': '2026-11-22', 'amount': 100, 'payment_method': 'Cash'})
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.current_balance, D('1100'))

    def test_transfer_to_the_same_account_is_rejected(self):
        r = self.post('/tasin-accounts/transfer', {'from_account_id': self.cash.pk, 'to_account_id': self.cash.pk, 'amount': 100})
        self.assertEqual(r.status_code, 422)
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.current_balance, D('1000'))

    def test_receipts_posted_in_the_same_second_get_distinct_transaction_ids(self):
        inv = self.invoice(custom_items=[{'description': 'Big job', 'quantity': 1, 'unit_price': 5000}]).json()
        for i in range(4):
            self.assertEqual(self.post(f'/tasin-invoices/{inv["id"]}/payments', {'date': '2026-11-22', 'amount': 100, 'payment_method': 'Cash', 'reference': f'R{i}'}).status_code, 200)
        from ..models import AccountTransaction
        ids = list(AccountTransaction.objects.filter(organization=self.org).values_list('transaction_id', flat=True))
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(set(ids)), 4)


class VehicleProfitabilityFixTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='QA2')
        u = User.objects.create_user('qa_fix2', password='x')
        Membership.objects.create(organization=self.org, user=u, role='admin', is_super_admin=True)
        self.c = Client(); self.c.force_login(u)
        self.base = f'/api/v1/orgs/{self.org.pk}'

    def post(self, path, body):
        return self.c.post(self.base + path, json.dumps(body), content_type='application/json')

    def test_owner_is_shown_and_cancelled_duties_earn_nothing(self):
        owner = self.post('/tasin-owners', {'name': 'Owner One', 'phone': '01700000001'}).json()['id']
        veh = self.post('/tasin-vehicles', {'registration': 'DHAKA-X-1', 'owner_id': owner, 'monthly_rent': 1000}).json()['id']
        base = {'date': '2026-11-10', 'client_name': 'C', 'vehicle_id': veh, 'vehicle_reg': 'DHAKA-X-1', 'driver_name': 'D', 'pickup_location': 'a', 'destination': 'b', 'start_time': '09:00'}
        d1 = self.post('/tasin-duties', dict(base, agreed_rent=5000)).json()['id']
        d2 = self.post('/tasin-duties', dict(base, date='2026-11-11', agreed_rent=3000)).json()['id']
        self.post(f'/tasin-duties/{d1}/status', {'status': 'Completed'})
        self.post(f'/tasin-duties/{d2}/status', {'status': 'Cancelled'})
        row = self.c.get(self.base + '/tasin-reports/vehicle-profitability').json()['vehicles'][0]
        self.assertEqual(row['owner'], 'Owner One')
        self.assertEqual(row['dutyIncome'], 5000.0)


class DriverVehicleLinkTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='QA3')
        self.other = Organization.objects.create(name='QA3-other')
        u = User.objects.create_user('qa_fix3', password='x')
        Membership.objects.create(organization=self.org, user=u, role='admin', is_super_admin=True)
        self.c = Client(); self.c.force_login(u)
        self.base = f'/api/v1/orgs/{self.org.pk}'
        self.owner = self.post('/tasin-owners', {'name': 'O', 'phone': '01700000001'}).json()['id']
        self.d1 = self.post('/tasin-drivers', {'name': 'D1', 'phone': '01700000002'}).json()['id']
        self.d2 = self.post('/tasin-drivers', {'name': 'D2', 'phone': '01700000003'}).json()['id']

    def post(self, path, body):
        return self.c.post(self.base + path, json.dumps(body), content_type='application/json')

    def put(self, path, body):
        return self.c.put(self.base + path, json.dumps(body), content_type='application/json')

    def vehicles(self):
        return {v['id']: v for v in self.c.get(self.base + '/tasin-vehicles').json()}

    def drivers(self):
        return {d['id']: d for d in self.c.get(self.base + '/tasin-drivers').json()}

    def test_assignment_is_visible_on_both_screens_and_moves_cleanly(self):
        v1 = self.post('/tasin-vehicles', {'registration': 'LINK-1', 'owner_id': self.owner, 'driver_id': self.d1}).json()['id']
        v2 = self.post('/tasin-vehicles', {'registration': 'LINK-2', 'owner_id': self.owner}).json()['id']
        self.assertEqual(self.vehicles()[v1]['driverId'], self.d1)
        self.assertEqual(self.drivers()[self.d1]['assignedVehicleId'], v1)                # the Drivers screen agrees
        self.assertEqual(self.drivers()[self.d1]['vehicleReg'], 'LINK-1')
        # move D1 to vehicle 2: vehicle 1 is released, nobody is double-booked
        self.assertEqual(self.put(f'/tasin-vehicles/{v2}', {'driver_id': self.d1}).status_code, 200)
        self.assertEqual(self.vehicles()[v1]['driverId'], '')
        self.assertEqual(self.vehicles()[v2]['driverId'], self.d1)
        self.assertEqual(self.drivers()[self.d1]['assignedVehicleId'], v2)
        # assigning from the driver side updates the vehicle side too, and replaces D1 on vehicle 2
        self.assertEqual(self.put(f'/tasin-drivers/{self.d2}', {'name': 'D2', 'phone': '01700000003', 'assigned_vehicle_id': int(v2)}).status_code, 200)
        self.assertEqual(self.vehicles()[v2]['driverId'], self.d2)
        self.assertEqual(self.drivers()[self.d1]['assignedVehicleId'], '')
        self.assertEqual(self.drivers()[self.d2]['assignedVehicleId'], v2)

    def test_ids_of_other_organizations_and_wrong_kinds_are_refused(self):
        from ..models import Party
        foreign_owner = Party.objects.create(organization=self.other, kind='owner', name='X')
        foreign_driver = Party.objects.create(organization=self.other, kind='driver', name='Y')
        v = self.post('/tasin-vehicles', {'registration': 'LINK-3', 'owner_id': self.owner}).json()['id']
        self.assertEqual(self.put(f'/tasin-vehicles/{v}', {'owner_id': foreign_owner.pk}).status_code, 422)
        self.assertEqual(self.put(f'/tasin-vehicles/{v}', {'driver_id': foreign_driver.pk}).status_code, 422)
        self.assertEqual(self.put(f'/tasin-vehicles/{v}', {'driver_id': self.owner}).status_code, 422)     # an owner is not a driver
        self.assertEqual(self.vehicles()[v]['ownerId'], self.owner)
        self.assertEqual(self.post('/tasin-vehicles', {'registration': 'LINK-4', 'owner_id': self.owner, 'driver_id': foreign_driver.pk}).status_code, 422)

    def test_partial_update_keeps_the_registration(self):
        v = self.post('/tasin-vehicles', {'registration': 'LINK-5', 'owner_id': self.owner, 'monthly_rent': 1000}).json()['id']
        self.assertEqual(self.put(f'/tasin-vehicles/{v}', {'monthly_rent': 2000}).status_code, 200)
        row = self.vehicles()[v]
        self.assertEqual((row['registration'], row['monthlyRent']), ('LINK-5', 2000.0))


class TenantIsolationUserManagementTests(TestCase):
    """An admin of organization A must never read, edit, reset or deactivate a user of organization B (found in QA: any user id worked)."""
    def setUp(self):
        self.a = Organization.objects.create(name='A')
        self.b = Organization.objects.create(name='B')
        self.admin = User.objects.create_user('a_super', password='x')
        Membership.objects.create(organization=self.a, user=self.admin, role='admin', is_super_admin=True)
        self.victim = User.objects.create_user('b_user', password='Original-Pass-1')
        Membership.objects.create(organization=self.b, user=self.victim, role='admin')
        self.mate = User.objects.create_user('a_mate', password='x')
        Membership.objects.create(organization=self.a, user=self.mate, role='staff')
        self.c = Client(); self.c.force_login(self.admin)
        self.base = f'/api/v1/orgs/{self.a.pk}/tasin-settings/users'

    def call(self, method, path, body=None):
        if method == 'get':
            return self.c.get(f'{self.base}/{path}')
        return getattr(self.c, method)(f'{self.base}/{path}', json.dumps(body or {}), content_type='application/json')

    def test_other_tenants_users_are_not_reachable(self):
        uid = self.victim.pk
        self.assertEqual(self.call('post', f'{uid}/reset-password', {'new_password': 'Hijacked-Pass-9'}).status_code, 404)
        self.assertEqual(self.call('put', f'{uid}/profile', {'name': 'Hijacked'}).status_code, 404)
        self.assertEqual(self.call('get', f'{uid}/permissions').status_code, 404)
        self.assertEqual(self.call('put', f'{uid}/permissions', {'permissions': {}}).status_code in (404, 422), True)
        self.assertEqual(self.call('delete', f'{uid}').status_code, 404)
        self.victim.refresh_from_db()
        self.assertTrue(self.victim.check_password('Original-Pass-1'))
        self.assertTrue(self.victim.is_active)
        self.assertTrue(Membership.objects.filter(user=self.victim, organization=self.b).exists())

    def test_own_members_can_still_be_managed_but_you_cannot_deactivate_yourself(self):
        self.assertEqual(self.call('post', f'{self.mate.pk}/reset-password', {'new_password': 'Fresh-Pass-12345'}).status_code, 200)
        self.assertEqual(self.call('delete', f'{self.admin.pk}').status_code, 422)
        self.assertEqual(self.call('delete', f'{self.mate.pk}').status_code, 200)


class ClientDeleteTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='QA4')
        u = User.objects.create_user('qa_fix4', password='x')
        Membership.objects.create(organization=self.org, user=u, role='admin', is_super_admin=True)
        self.c = Client(); self.c.force_login(u)
        self.base = f'/api/v1/orgs/{self.org.pk}'

    def post(self, path, body):
        return self.c.post(self.base + path, json.dumps(body), content_type='application/json')

    def test_client_with_history_returns_409_not_a_server_error(self):
        cid = self.post('/tasin-clients', {'name': 'C', 'contact_person': 'P', 'phone': '01700000009', 'opening_balance': 500, 'opening_balance_type': 'receivable'}).json()['id']
        r = self.c.delete(f'{self.base}/tasin-clients/{cid}')
        self.assertEqual(r.status_code, 409, r.content)
        free = self.post('/tasin-clients', {'name': 'Free', 'contact_person': 'P', 'phone': '01700000010'}).json()['id']
        self.assertEqual(self.c.delete(f'{self.base}/tasin-clients/{free}').status_code, 200)


class ExpiringPaperDashboardTests(TestCase):
    """QA found the whole Dashboard returned a 500 as soon as a vehicle paper was within 30 days of expiry (alert code used a field that does not exist)."""
    def setUp(self):
        self.org = Organization.objects.create(name='QA5')
        u = User.objects.create_user('qa_fix5', password='x')
        Membership.objects.create(organization=self.org, user=u, role='admin', is_super_admin=True)
        self.c = Client(); self.c.force_login(u)
        self.base = f'/api/v1/orgs/{self.org.pk}'

    def post(self, path, body):
        return self.c.post(self.base + path, json.dumps(body), content_type='application/json')

    def test_dashboard_and_alerts_work_with_expiring_and_expired_papers(self):
        from datetime import date, timedelta
        owner = self.post('/tasin-owners', {'name': 'O', 'phone': '01700000001'}).json()['id']
        veh = self.post('/tasin-vehicles', {'registration': 'EXP-1', 'owner_id': owner}).json()['id']
        for kind, days in (('fitness', 10), ('tax_token', -5), ('insurance', 400)):
            r = self.post(f'/tasin-vehicles/{veh}/papers', {'paper_type': kind, 'paper_number': 'N-' + kind, 'expiry_date': (date.today() + timedelta(days=days)).isoformat()})
            self.assertEqual(r.status_code, 200, r.content)
        r = self.c.get(self.base + '/dashboard/kpis')
        self.assertEqual(r.status_code, 200, r.content[:300])
        titles = ' | '.join(a['title'] for a in r.json()['alerts'])
        self.assertIn('EXP-1', titles)
        self.assertEqual(self.c.get(self.base + '/tasin-alerts').status_code, 200)
