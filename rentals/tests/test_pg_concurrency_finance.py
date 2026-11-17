"""Concurrency tests for the party ledger. They need REAL PostgreSQL (row locks are no-ops on SQLite) and are skipped otherwise.

Run against a disposable PostgreSQL or a Neon *branch* - never production:
    DATABASE_URL=postgres://... DJANGO_DEBUG=1 python manage.py test rentals.tests.test_pg_concurrency_finance --keepdb
Each worker thread uses its own Django test client, hence its own database connection and transaction.
"""
import json
import threading
from datetime import date
from decimal import Decimal as D
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections
from django.test import TransactionTestCase, Client
from rentals.models import (Organization, Branch, Membership, Party, PartyDetail, Vehicle, VehicleDetail, SalaryPayment, RentAccrual,
                            PartyLedgerEntry, AccountTransaction)
from rentals.services import finance as fin

IS_PG = connection.vendor == 'postgresql'


def run_parallel(n, fn):
    """Start n threads at the same instant; return their results in order."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        try:
            c = Client()
            c.force_login(get_user_model().objects.get(username='admin'))
            barrier.wait(timeout=20)
            results[i] = fn(i, c)
        except Exception as exc:  # surfaced by the assertions below
            results[i] = exc
        finally:
            close_old_connections()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return results


@skipUnless(IS_PG, 'needs PostgreSQL: SQLite ignores select_for_update')
class LedgerConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('admin', password='x')
        self.org = Organization.objects.create(name='TASIN')
        branch = Branch.objects.create(organization=self.org, name='Main')
        Membership.objects.create(organization=self.org, user=self.admin, role='admin', branch=branch)
        self.base = f'/api/v1/orgs/{self.org.pk}'
        self.driver = Party.objects.create(organization=self.org, kind='driver', name='D')
        PartyDetail.objects.create(party=self.driver, monthly_salary=D('20000'))
        self.owner = Party.objects.create(organization=self.org, kind='owner', name='O')
        PartyDetail.objects.create(party=self.owner)
        self.client_party = Party.objects.create(organization=self.org, kind='customer', name='C')
        PartyDetail.objects.create(party=self.client_party)
        self.cars = []
        for i in range(3):
            v = Vehicle.objects.create(organization=self.org, registration=f'CAR-{i}', model_name='m', owner=self.owner)
            VehicleDetail.objects.create(vehicle=v, monthly_rent=D('10000'), agreement_start=date(2026, 1, 1), status='On Duty')
            self.cars.append(v)

    def post(self, c, path, data, key=None):
        extra = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return c.post(self.base + path, data=json.dumps(data), content_type='application/json', **extra)

    def codes(self, results):
        for r in results:
            self.assertNotIsInstance(r, Exception, r)
        return sorted(r.status_code for r in results)

    def assert_reconciled(self):
        self.assertEqual(fin.reconcile_ledger(self.org), [])

    # ---- salary / advance / fuel-gas
    def test_concurrent_salary_payments_same_month_different_keys(self):
        fin.set_opening_balance(self.org, self.driver, self.admin, 'advance', D('10000'))
        res = run_parallel(8, lambda i, c: self.post(c, f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'advance_adjustment': '5000'}, key=f'k{i}'))
        codes = self.codes(res)
        self.assertEqual(codes.count(200), 1, codes)
        self.assertEqual(SalaryPayment.objects.filter(status='posted').count(), 1)
        self.assertEqual(fin.balance(self.driver, 'driver_advance'), D('5000'))      # adjusted once
        self.assertEqual(AccountTransaction.objects.filter(category='Driver Salary').count(), 1)
        self.assert_reconciled()

    def test_concurrent_salary_payments_same_idempotency_key(self):
        res = run_parallel(8, lambda i, c: self.post(c, f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30'}, key='same'))
        self.assertTrue(all(r.status_code == 200 for r in res if not isinstance(r, Exception)), [getattr(r, 'status_code', r) for r in res])
        self.assertEqual(SalaryPayment.objects.count(), 1)
        self.assert_reconciled()

    def test_concurrent_advance_adjustments_across_months_never_overdraw(self):
        fin.set_opening_balance(self.org, self.driver, self.admin, 'advance', D('10000'))
        months = ['2026-09', '2026-10', '2026-11', '2026-12']
        res = run_parallel(4, lambda i, c: self.post(c, f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': months[i], 'payment_date': '2026-12-30', 'advance_adjustment': '6000'}, key=f'm{i}'))
        codes = self.codes(res)
        self.assertEqual(codes.count(200), 1, codes)                                 # only one 6,000 fits in 10,000
        self.assertEqual(fin.balance(self.driver, 'driver_advance'), D('4000'))
        self.assert_reconciled()

    def test_concurrent_fuel_gas_settlements_never_overdraw(self):
        fin.set_opening_balance(self.org, self.driver, self.admin, 'fuel_gas', D('5000'))
        months = ['2026-09', '2026-10', '2026-11']
        res = run_parallel(3, lambda i, c: self.post(c, f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': months[i], 'payment_date': '2026-12-30', 'fuel_gas_settlement': '3000', 'fuel_gas_mode': 'cash'}, key=f'f{i}'))
        self.assertEqual(self.codes(res).count(200), 1)
        self.assertEqual(fin.balance(self.driver, 'driver_fuel_gas'), D('2000'))
        self.assert_reconciled()

    # ---- rent accrual
    def test_concurrent_rent_accrual_posts_each_vehicle_once(self):
        res = run_parallel(8, lambda i, c: self.post(c, '/rent-accruals', {'period': '2026-11'}))
        self.codes(res)
        self.assertEqual(RentAccrual.objects.filter(status='posted', period='2026-11').count(), 3)
        self.assertEqual(fin.balance(self.owner, 'owner_payable'), D('30000'))
        self.assert_reconciled()

    # ---- payments / collections
    def test_concurrent_owner_payments_cannot_exceed_payable(self):
        fin.set_opening_balance(self.org, self.owner, self.admin, 'payable', D('100000'))
        res = run_parallel(6, lambda i, c: self.post(c, f'/owners/{self.owner.pk}/ledger-payments', {'amount': '60000', 'date': '2026-11-20', 'reference': f'P{i}'}, key=f'o{i}'))
        self.assertEqual(self.codes(res).count(200), 1)
        self.assertEqual(fin.balance(self.owner, 'owner_payable'), D('40000'))
        self.assert_reconciled()

    def test_concurrent_client_collections_cannot_over_collect(self):
        fin.set_opening_balance(self.org, self.client_party, self.admin, 'receivable', D('100000'))
        res = run_parallel(6, lambda i, c: self.post(c, f'/clients/{self.client_party.pk}/ledger-payments', {'amount': '40000', 'date': '2026-11-20'}, key=f'c{i}'))
        self.assertEqual(self.codes(res).count(200), 2)                              # 2 x 40,000 fit, a third would exceed
        self.assertEqual(fin.balance(self.client_party, 'client_receivable'), D('20000'))
        self.assert_reconciled()

    def test_duplicate_submissions_of_same_owner_payment_key(self):
        fin.set_opening_balance(self.org, self.owner, self.admin, 'payable', D('100000'))
        run_parallel(8, lambda i, c: self.post(c, f'/owners/{self.owner.pk}/ledger-payments', {'amount': '10000', 'date': '2026-11-20'}, key='dup'))
        self.assertEqual(fin.balance(self.owner, 'owner_payable'), D('90000'))
        self.assertEqual(PartyLedgerEntry.objects.filter(party=self.owner, entry_type='payment').count(), 1)
        self.assert_reconciled()

    # ---- rollback and constraints
    def test_failed_salary_payment_rolls_back_everything(self):
        fin.set_opening_balance(self.org, self.driver, self.admin, 'advance', D('1000'))
        before = (PartyLedgerEntry.objects.count(), AccountTransaction.objects.count(), SalaryPayment.objects.count())
        c = Client()
        c.force_login(self.admin)
        r = self.post(c, f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'advance_adjustment': '1000', 'amount_paid': '999999'})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(before, (PartyLedgerEntry.objects.count(), AccountTransaction.objects.count(), SalaryPayment.objects.count()))

    def test_database_constraints_reject_duplicates_even_without_the_service(self):
        from django.db import IntegrityError, transaction
        RentAccrual.objects.create(organization=self.org, owner=self.owner, vehicle=self.cars[0], period='2026-11', amount=D('1'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            RentAccrual.objects.create(organization=self.org, owner=self.owner, vehicle=self.cars[0], period='2026-11', amount=D('1'))
        PartyLedgerEntry.objects.create(organization=self.org, party=self.owner, account='owner_payable', entry_type='opening', delta=D('1'), date=date.today(), unique_key='k')
        with self.assertRaises(IntegrityError), transaction.atomic():
            PartyLedgerEntry.objects.create(organization=self.org, party=self.owner, account='owner_payable', entry_type='opening', delta=D('1'), date=date.today(), unique_key='k')
        with self.assertRaises(IntegrityError), transaction.atomic():
            PartyLedgerEntry.objects.create(organization=self.org, party=self.owner, account='owner_payable', entry_type='opening', delta=D('0'), date=date.today())
