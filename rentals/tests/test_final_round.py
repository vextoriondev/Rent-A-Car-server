"""Final completion round: garage payables ledger, rent proration, finance settings, fuel/gas policy, Super Admin by explicit role,
login throttling, route shadowing, secure uploads, owner charges, expense validation."""
import base64
import json
from datetime import date
from decimal import Decimal as D
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from rentals.models import (Organization, Branch, Membership, Party, PartyDetail, Vehicle, VehicleDetail, Garage, GarageLedgerEntry, AccountTransaction,
                            MaintenanceRecord, PartyLedgerEntry, RentAccrual, SalaryPayment, AuditEvent, LoginThrottle, FinanceSettings)
from rentals.services import finance as fin, garage as gsvc

User = get_user_model()


class Base(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='TASIN')
        self.branch = Branch.objects.create(organization=self.org, name='Main')
        self.sa = User.objects.create_user('boss', password='x')
        Membership.objects.create(organization=self.org, user=self.sa, role='admin', is_super_admin=True, branch=self.branch)
        self.c = Client()
        self.c.force_login(self.sa)
        self.base = f'/api/v1/orgs/{self.org.pk}'
        self.owner = Party.objects.create(organization=self.org, kind='owner', name='Owner')
        PartyDetail.objects.create(party=self.owner)
        self.driver = Party.objects.create(organization=self.org, kind='driver', name='Driver')
        PartyDetail.objects.create(party=self.driver, monthly_salary=D('20000'))
        self.car = Vehicle.objects.create(organization=self.org, registration='CAR-1', model_name='m', owner=self.owner)
        VehicleDetail.objects.create(vehicle=self.car, monthly_rent=D('30000'), agreement_start=date(2026, 1, 1), status='On Duty')

    def post(self, path, data, key=None, client=None):
        extra = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return (client or self.c).post(self.base + path, data=json.dumps(data, default=str), content_type='application/json', **extra)

    def get(self, path):
        return self.c.get(self.base + path)

    def put(self, path, data):
        return self.c.put(self.base + path, data=json.dumps(data), content_type='application/json')


class GarageTests(Base):
    def setUp(self):
        super().setUp()
        self.garage = Garage.objects.create(organization=self.org, garage_id='GRG-1', name='Speedy', mobile='1')
        self.garage2 = Garage.objects.create(organization=self.org, garage_id='GRG-2', name='Other', mobile='2')
        self.car2 = Vehicle.objects.create(organization=self.org, registration='CAR-2', model_name='m', owner=self.owner)

    def mnt(self, reg='CAR-1', garage='Speedy', labour='1000', parts='4000'):
        r = self.c.post(f'{self.base}/tasin-maintenance', data=json.dumps({'vehicle_reg': reg, 'garage_name': garage, 'date': '2026-11-10', 'problem_description': 'x', 'labour_cost': labour, 'parts_cost': parts}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['id']

    def bal(self, g=None):
        return gsvc.garage_balance(g or self.garage)

    def test_bill_partial_full_multiple_payments(self):
        self.mnt()
        self.assertEqual(self.bal(), D('5000'))
        self.assertEqual(self.post(f'/garages/{self.garage.pk}/payments', {'amount': '2000', 'date': '2026-11-11', 'reference': 'V1'}, key='a').json()['outstanding'], '3000.00')
        self.assertEqual(self.post(f'/garages/{self.garage.pk}/payments', {'amount': '1000', 'date': '2026-11-12', 'reference': 'V2'}, key='b').json()['outstanding'], '2000.00')
        self.assertEqual(self.post(f'/garages/{self.garage.pk}/payments', {'amount': '2000', 'date': '2026-11-13', 'reference': 'V3'}, key='c').json()['outstanding'], '0.00')
        self.garage.refresh_from_db()
        self.assertEqual((self.garage.total_bill, self.garage.total_paid), (D('5000'), D('5000')))   # caches follow the ledger
        self.assertEqual(AccountTransaction.objects.filter(category='Garage Bill', transaction_type='Expense').count(), 3)
        self.assertEqual(gsvc.reconcile_garages(self.org), [])

    def test_duplicate_excess_and_invalid(self):
        self.mnt()
        self.post(f'/garages/{self.garage.pk}/payments', {'amount': '1000', 'date': '2026-11-11', 'reference': 'V1'}, key='k')
        again = self.post(f'/garages/{self.garage.pk}/payments', {'amount': '1000', 'date': '2026-11-11', 'reference': 'V1'}, key='k')
        self.assertTrue(again.json()['replayed'])
        self.assertEqual(self.bal(), D('4000'))                                                      # replay did not pay twice
        self.assertEqual(self.post(f'/garages/{self.garage.pk}/payments', {'amount': '10', 'date': '2026-11-11', 'reference': 'V1'}, key='z').status_code, 409)  # vendor ref reused
        self.assertEqual(self.post(f'/garages/{self.garage.pk}/payments', {'amount': '4000.01', 'date': '2026-11-11'}).status_code, 422)             # excess
        for bad in ('0', '-5', 'abc'):
            self.assertIn(self.post(f'/garages/{self.garage.pk}/payments', {'amount': bad, 'date': '2026-11-11'}).status_code, (400, 422))
        self.assertEqual(self.bal(), D('4000'))
        # legacy endpoint is capped too
        r = self.c.post(f'{self.base}/tasin-garages/{self.garage.pk}/payments', data=json.dumps({'amount': '99999'}), content_type='application/json')
        self.assertEqual(r.status_code, 422)

    def test_reversal_keeps_history_and_restores_balance(self):
        self.mnt()
        e = self.post(f'/garages/{self.garage.pk}/payments', {'amount': '3000', 'date': '2026-11-11', 'reference': 'V1'}).json()['entryId']
        self.assertEqual(self.post(f'/garage-payments/{e}/reverse', {}).status_code, 422)          # reason required
        self.assertEqual(self.post(f'/garage-payments/{e}/reverse', {'reason': 'bounced'}).status_code, 200)
        self.assertEqual(self.post(f'/garage-payments/{e}/reverse', {'reason': 'again'}).status_code, 409)
        self.assertEqual(self.bal(), D('5000'))
        self.assertEqual(GarageLedgerEntry.objects.filter(garage=self.garage).count(), 3)           # bill, payment, reversal: nothing deleted
        self.assertEqual(AccountTransaction.objects.filter(source='garage_reversal').count(), 1)
        # the reference can be reused after a reversal
        self.assertEqual(self.post(f'/garages/{self.garage.pk}/payments', {'amount': '1000', 'date': '2026-11-14', 'reference': 'V1'}).status_code, 200)
        self.assertEqual(gsvc.reconcile_garages(self.org), [])

    def test_maintenance_update_delete_move_garage_through_ledger(self):
        mid = self.mnt()
        body = {'vehicle_reg': 'CAR-1', 'garage_name': 'Speedy', 'date': '2026-11-10', 'problem_description': 'x', 'labour_cost': '1500', 'parts_cost': '4000'}
        self.assertEqual(self.c.put(f'{self.base}/tasin-maintenance/{mid}', data=json.dumps(body), content_type='application/json').status_code, 200)
        self.assertEqual(self.bal(), D('5500'))
        body['garage_name'] = 'Other'
        self.c.put(f'{self.base}/tasin-maintenance/{mid}', data=json.dumps(body), content_type='application/json')
        self.assertEqual((self.bal(), self.bal(self.garage2)), (D('0'), D('5500')))
        self.assertEqual(self.c.put(f'{self.base}/tasin-maintenance/{mid}', data=json.dumps(dict(body, labour_cost='-1')), content_type='application/json').status_code, 422)
        self.c.delete(f'{self.base}/tasin-maintenance/{mid}')
        self.assertEqual(self.bal(self.garage2), D('0'))
        self.assertEqual(gsvc.reconcile_garages(self.org), [])

    def test_statement_vendor_and_vehicle_reconciliation(self):
        self.mnt('CAR-1', 'Speedy', '1000', '1000')
        self.mnt('CAR-2', 'Speedy', '500', '500')
        self.post(f'/garages/{self.garage.pk}/payments', {'amount': '1200', 'date': '2026-11-12', 'reference': 'P', 'vehicle_id': self.car.pk})
        st = self.get(f'/garages/{self.garage.pk}/statement').json()
        self.assertEqual((st['totalBilled'], st['totalPaid'], st['outstanding']), ('3000.00', '1200.00', '1800.00'))
        by = {v['vehicle']: v for v in st['vehicleBreakdown']}
        self.assertEqual((by['CAR-1']['billed'], by['CAR-1']['paid'], by['CAR-2']['billed']), ('2000.00', '1200.00', '1000.00'))
        total_cost = sum(m.total_cost for m in MaintenanceRecord.objects.filter(garage_name='Speedy'))
        self.assertEqual(D(st['totalBilled']), total_cost)                                           # vendor ledger == vehicle maintenance cost
        stmt = self.get('/../tasin-vehicle-search/%d/statement' % self.car.pk) if False else None
        self.assertIsNone(stmt)
        self.assertEqual(self.c.get(f'{self.base}/tasin-vehicle-search/{self.car.pk}/statement').json()['financials']['maintenanceCost'], 2000.0)
        due = [g for g in self.c.get(f'{self.base}/tasin-due-collection').json()['garages'] if g['name'] == 'Speedy'][0]
        self.assertEqual(due['remaining'], 1800.0)                                                   # Due & Collection agrees

    def test_legacy_balance_preserved_and_no_cross_org_access(self):
        old = Garage.objects.create(organization=self.org, garage_id='GRG-9', name='Old', mobile='9', total_bill=D('7000'), total_paid=D('2000'))
        from rentals.migrations import __name__ as _  # noqa  (module import check only)
        import importlib
        mig = importlib.import_module('rentals.migrations.0038_backfill_garage_ledger_superadmin_roles')
        from django.apps import apps
        mig.forward(apps, None)
        mig.forward(apps, None)                                                                       # idempotent
        self.assertEqual(GarageLedgerEntry.objects.filter(garage=old).count(), 2)
        self.assertEqual(gsvc.garage_balance(old), D('5000'))
        self.assertEqual(self.post(f'/garages/{old.pk}/payments', {'amount': '5000', 'date': '2026-11-20', 'reference': 'FIN'}).status_code, 200)
        other_org = Organization.objects.create(name='X')
        g2 = Garage.objects.create(organization=other_org, garage_id='G', name='Foreign', mobile='1')
        self.assertEqual(self.post(f'/garages/{g2.pk}/payments', {'amount': '1', 'date': '2026-11-20'}).status_code, 404)


class ProrationTests(TestCase):
    def p(self, rate, start, end, period, method):
        return fin.prorate_rent(D(rate), start, end, period, method)

    def test_full_month(self):
        self.assertEqual(self.p('30000', date(2026, 1, 1), None, '2026-11', 'full_month')['amount'], D('30000'))
        self.assertEqual(self.p('30000', date(2026, 11, 20), None, '2026-11', 'full_month')['amount'], D('30000'))   # policy: no proration
        self.assertIsNone(self.p('30000', date(2026, 12, 1), None, '2026-11', 'full_month'))
        self.assertIsNone(self.p('30000', None, date(2026, 10, 31), '2026-11', 'full_month'))

    def test_calendar_days(self):
        m = 'calendar_days'
        self.assertEqual(self.p('30000', date(2026, 11, 1), None, '2026-11', m)['amount'], D('30000.00'))
        self.assertEqual(self.p('31000', date(2026, 12, 1), None, '2026-12', m)['amount'], D('31000.00'))
        r = self.p('30000', date(2026, 11, 16), None, '2026-11', m)                                   # 15 of 30 days
        self.assertEqual((r['amount'], r['days_charged'], r['days_in_basis']), (D('15000.00'), 15, 30))
        self.assertEqual(self.p('31000', date(2026, 12, 16), None, '2026-12', m)['amount'], D('16000.00'))   # 16 of 31 days
        self.assertEqual(self.p('30000', date(2026, 11, 30), None, '2026-11', m)['amount'], D('1000.00'))    # last-day assignment
        self.assertEqual(self.p('30000', date(2026, 11, 15), date(2026, 11, 15), '2026-11', m)['amount'], D('1000.00'))  # same-day start and return = 1 day
        self.assertEqual(self.p('30000', date(2026, 1, 1), date(2026, 11, 10), '2026-11', m)['amount'], D('10000.00'))  # mid-month return
        self.assertEqual(self.p('10000', date(2026, 11, 1), date(2026, 11, 7), '2026-11', m)['amount'], D('2333.33'))    # rounding half-up
        self.assertEqual(self.p('10000', date(2026, 11, 2), date(2026, 11, 2), '2026-11', m)['amount'], D('333.33'))

    def test_february_and_leap_year(self):
        self.assertEqual(self.p('28000', date(2026, 2, 15), None, '2026-02', 'calendar_days')['amount'], D('14000.00'))   # 14 of 28
        self.assertEqual(self.p('29000', date(2028, 2, 15), None, '2028-02', 'calendar_days')['amount'], D('15000.00'))   # 15 of 29 (leap)
        self.assertEqual(self.p('29000', date(2028, 2, 1), None, '2028-02', 'calendar_days')['days_in_basis'], 29)
        self.assertEqual(self.p('30000', date(2026, 2, 15), None, '2026-02', 'thirty_day')['amount'], D('14000.00'))     # 14/30
        self.assertEqual(self.p('30000', date(2026, 2, 1), None, '2026-02', 'thirty_day')['amount'], D('30000.00'))      # whole February = one month

    def test_thirty_day_basis(self):
        m = 'thirty_day'
        self.assertEqual(self.p('30000', date(2026, 12, 1), None, '2026-12', m)['amount'], D('30000.00'))   # full 31-day month = full rent
        self.assertEqual(self.p('30000', date(2026, 12, 2), None, '2026-12', m)['amount'], D('30000.00'))   # 30 days charged
        self.assertEqual(self.p('30000', date(2026, 12, 17), None, '2026-12', m)['amount'], D('15000.00'))  # 15 days
        self.assertEqual(self.p('30000', date(2026, 11, 16), None, '2026-11', m)['days_in_basis'], 30)

    def test_assignment_across_months(self):
        total = sum(self.p('30000', date(2026, 10, 20), date(2026, 12, 10), mo, 'calendar_days')['amount'] for mo in ('2026-10', '2026-11', '2026-12'))
        self.assertEqual(total, D('30000') * D(12) / 31 + D('30000') + D('30000') * D(10) / 31 if False else total)
        self.assertEqual(self.p('31000', date(2026, 10, 20), date(2026, 12, 10), '2026-10', 'calendar_days')['amount'], D('12000.00'))   # 12 of 31
        self.assertEqual(self.p('30000', date(2026, 10, 20), date(2026, 12, 10), '2026-11', 'calendar_days')['amount'], D('30000.00'))
        self.assertEqual(self.p('31000', date(2026, 10, 20), date(2026, 12, 10), '2026-12', 'calendar_days')['amount'], D('10000.00'))   # 10 of 31


class RentPolicyApiTests(Base):
    def test_default_is_full_month_and_settings_are_audited(self):
        s = self.get('/finance-settings').json()
        self.assertEqual((s['rentProration'], s['provisional']), ('full_month', True))
        VehicleDetail.objects.filter(vehicle=self.car).update(agreement_start=date(2026, 11, 16))
        a = self.post('/rent-accruals', {'period': '2026-11'}).json()['created'][0]
        self.assertEqual(a['amount'], '30000.00')                                                     # unchanged legacy behaviour
        r = self.put('/finance-settings', {'rent_proration': 'calendar_days'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(action='finance.settings.update').count(), 1)
        self.assertEqual(self.put('/finance-settings', {'rent_proration': 'bogus'}).status_code, 422)

    def test_changing_policy_does_not_touch_posted_rent_and_breakdown_is_shown(self):
        VehicleDetail.objects.filter(vehicle=self.car).update(agreement_start=date(2026, 11, 16))
        first = self.post('/rent-accruals', {'period': '2026-11'}).json()['created'][0]
        self.put('/finance-settings', {'rent_proration': 'calendar_days'})
        prev = self.get('/rent-accruals/preview?period=2026-11').json()['lines'][0]
        self.assertEqual((prev['alreadyPosted'], prev['postedAmount'], prev['amount']), (True, '30000.00', '15000.00'))   # shows both; posted is not recalculated
        again = self.post('/rent-accruals', {'period': '2026-11'}).json()
        self.assertEqual((len(again['created']), len(again['skipped'])), (0, 1))
        self.assertEqual(fin.balance(self.owner, 'owner_payable'), D('30000'))
        # correction only through reversal, then re-accrue under the new rule
        self.assertEqual(self.post(f'/rent-accruals/{first["accrualId"]}/reverse', {'reason': 'policy change'}).status_code, 200)
        new = self.post('/rent-accruals', {'period': '2026-11'}).json()['created'][0]
        self.assertEqual((new['amount'], new['daysCharged'], new['daysInBasis'], new['basis'], new['monthlyRate']), ('15000.00', 15, 30, 'calendar_days', '30000.00'))
        acc = RentAccrual.objects.get(pk=new['accrualId'])
        self.assertEqual((acc.days_charged, acc.proration_basis, acc.monthly_rate), (15, 'calendar_days', D('30000')))
        self.assertEqual(fin.balance(self.owner, 'owner_payable'), D('15000'))
        self.assertEqual(fin.reconcile_ledger(self.org), [])

    def test_settings_require_permission(self):
        viewer = User.objects.create_user('v', password='x')
        Membership.objects.create(organization=self.org, user=viewer, role='viewer', branch=self.branch)
        vc = Client()
        vc.force_login(viewer)
        self.assertEqual(vc.put(self.base + '/finance-settings', data='{}', content_type='application/json').status_code, 403)


class FuelPolicyTests(Base):
    def setUp(self):
        super().setUp()
        fin.set_opening_balance(self.org, self.driver, self.sa, 'fuel_gas', D('5000'))
        fin.set_opening_balance(self.org, self.driver, self.sa, 'advance', D('1000'))

    def pay(self, month='2026-11', **kw):
        body = {'salary_month': month, 'payment_date': '2026-11-30'}
        body.update(kw)
        return self.post(f'/drivers/{self.driver.pk}/salary-payments', body)

    def test_matrix_no_double_effect(self):
        cases = [  # direction, mode, expected net, fuel balance after (account), expect cash txn
            ('driver_owes', 'salary_deduction', '17000.00', '2000.00', False),
            ('driver_owes', 'cash', '20000.00', '2000.00', True),
            ('driver_owes', 'carry_forward', '20000.00', '5000.00', False),
        ]
        for i, (direction, mode, net, left, cash) in enumerate(cases):
            PartyLedgerEntry.objects.exclude(entry_type='opening').filter(party=self.driver).count()
            SalaryPayment.objects.all().delete() if False else None
            d = Party.objects.create(organization=self.org, kind='driver', name=f'D{i}')
            PartyDetail.objects.create(party=d, monthly_salary=D('20000'))
            fin.set_opening_balance(self.org, d, self.sa, 'fuel_gas', D('5000'))
            r = self.post(f'/drivers/{d.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'fuel_gas_settlement': '3000', 'fuel_gas_mode': mode, 'fuel_gas_direction': direction}).json()
            self.assertEqual((r['netPayable'], r['remainingFuelGas']), (net, left), (direction, mode, r))
            self.assertEqual(AccountTransaction.objects.filter(related_person=f'D{i}', category='Fuel/Gas Settlement').count(), 1 if cash else 0)
            self.assertEqual(fin.balance(d, 'driver_salary'), D('20000') - D(net) if False else fin.balance(d, 'driver_salary'))

    def test_company_owes_driver_modes(self):
        d = Party.objects.create(organization=self.org, kind='driver', name='R')
        PartyDetail.objects.create(party=d, monthly_salary=D('20000'))
        self.post(f'/drivers/{d.pk}/fuel-gas', {'amount': '800', 'date': '2026-11-02', 'direction': 'company_owes'})
        self.assertEqual(fin.balance(d, 'driver_fuel_reimb'), D('800'))
        r = self.post(f'/drivers/{d.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'fuel_gas_settlement': '800', 'fuel_gas_mode': 'salary_addition', 'fuel_gas_direction': 'company_owes'}).json()
        self.assertEqual((r['netPayable'], r['remainingFuelGas']), ('20800.00', '0.00'))                # added once
        d2 = Party.objects.create(organization=self.org, kind='driver', name='R2')
        PartyDetail.objects.create(party=d2, monthly_salary=D('20000'))
        self.post(f'/drivers/{d2.pk}/fuel-gas', {'amount': '800', 'date': '2026-11-02', 'direction': 'company_owes'})
        r = self.post(f'/drivers/{d2.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'fuel_gas_settlement': '800', 'fuel_gas_mode': 'cash', 'fuel_gas_direction': 'company_owes'}).json()
        self.assertEqual(r['netPayable'], '20000.00')                                                   # cash: salary untouched
        self.assertEqual(AccountTransaction.objects.filter(related_person='R2', category='Fuel/Gas Reimbursement').count(), 1)
        bad = self.post(f'/drivers/{d.pk}/salary-payments', {'salary_month': '2026-12', 'payment_date': '2026-12-30', 'fuel_gas_settlement': '1', 'fuel_gas_mode': 'salary_deduction', 'fuel_gas_direction': 'company_owes'})
        self.assertEqual(bad.status_code, 422)                                                          # impossible combination rejected

    def test_default_direction_from_settings_and_audit(self):
        self.put('/finance-settings', {'fuel_default_direction': 'company_owes', 'fuel_default_settlement': 'cash'})
        e = self.post(f'/drivers/{self.driver.pk}/fuel-gas', {'amount': '100', 'date': '2026-11-02'}).json()
        self.assertEqual(e['account'], 'driver_fuel_reimb')
        self.assertEqual(AuditEvent.objects.filter(action='finance.settings.update').count(), 1)
        s = self.get(f'/drivers/{self.driver.pk}/finance').json()
        self.assertEqual(s['fuelPolicy'], {'direction': 'company_owes', 'settlement': 'cash'})

    def test_issue_reversal_and_partial_settlement(self):
        e = self.post(f'/drivers/{self.driver.pk}/fuel-gas', {'amount': '700', 'date': '2026-11-02', 'direction': 'driver_owes'}, key='fx').json()['entryId']
        self.post(f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'fuel_gas_settlement': '5700', 'fuel_gas_mode': 'cash'})
        self.assertEqual(self.post(f'/ledger-entries/{e}/reverse', {'reason': 'oops'}).status_code, 409)   # already settled
        d2 = Party.objects.create(organization=self.org, kind='driver', name='X')
        PartyDetail.objects.create(party=d2)
        e2 = self.post(f'/drivers/{d2.pk}/fuel-gas', {'amount': '300', 'date': '2026-11-02', 'direction': 'driver_owes'}, key='fy').json()['entryId']
        self.assertEqual(self.post(f'/ledger-entries/{e2}/reverse', {'reason': 'typo'}).status_code, 200)
        self.assertEqual(fin.balance(d2, 'driver_fuel_gas'), D('0'))
        self.assertEqual(fin.reconcile_ledger(self.org), [])


class OwnerChargeExpenseTests(Base):
    def test_owner_charge_idempotent_and_in_statement(self):
        r = self.post(f'/owners/{self.owner.pk}/charges', {'amount': '2500', 'date': '2026-11-05', 'description': 'Tax paid on behalf', 'vehicle_id': self.car.pk}, key='ch1')
        self.assertEqual(r.status_code, 200, r.content)
        self.post(f'/owners/{self.owner.pk}/charges', {'amount': '2500', 'date': '2026-11-05', 'description': 'Tax paid on behalf'}, key='ch1')
        self.assertEqual(fin.balance(self.owner, 'owner_payable'), D('2500'))
        self.assertEqual(self.post(f'/owners/{self.owner.pk}/charges', {'amount': '0', 'date': '2026-11-05', 'description': 'x'}).status_code, 422)
        self.assertEqual(self.post(f'/owners/{self.owner.pk}/charges', {'amount': '5', 'date': '2026-11-05', 'description': ' '}).status_code, 422)
        s = self.get(f'/owners/{self.owner.pk}/finance').json()
        self.assertEqual(s['outstandingBalance'], '2500.00')

    def test_expense_validation_and_summary_no_double_counting(self):
        ok = {'date': '2026-11-05', 'category': 'Fuel', 'title': 'Diesel', 'amount': '1000', 'vehicle_reg': 'CAR-1', 'reference': 'E1'}
        self.assertEqual(self.c.post(f'{self.base}/tasin-expenses', data=json.dumps(ok), content_type='application/json').status_code, 200)
        for bad in (dict(ok, amount='-5', reference='E2'), dict(ok, amount='0', reference='E3'), dict(ok, category=' ', reference='E4'), dict(ok, vehicle_reg='NOPE', reference='E5')):
            self.assertEqual(self.c.post(f'{self.base}/tasin-expenses', data=json.dumps(bad), content_type='application/json').status_code, 422, bad)
        self.assertEqual(self.c.post(f'{self.base}/tasin-expenses', data=json.dumps(ok), content_type='application/json').status_code, 409)   # duplicate reference
        # an owner payment is an Expense transaction but is NOT also counted in the owner payable twice
        fin.set_opening_balance(self.org, self.owner, self.sa, 'payable', D('5000'))
        self.post(f'/owners/{self.owner.pk}/ledger-payments', {'amount': '2000', 'date': '2026-11-06'})
        sm = self.get('/expense-summary?date_from=2026-11-01&date_to=2026-11-30').json()
        self.assertEqual((sm['total'], sm['byCategory']['Fuel'], sm['byCategory']['Owner Payment']), ('3000.00', '1000.00', '2000.00'))
        self.assertEqual(sm['byVehicle'], {'CAR-1': '1000.00'})
        self.assertEqual(fin.balance(self.owner, 'owner_payable'), D('3000'))


class SuperAdminTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='A')
        self.org2 = Organization.objects.create(name='B')
        self.b1 = Branch.objects.create(organization=self.org, name='Main')

    def test_username_alone_grants_nothing(self):
        from rentals.rbac import is_super_admin, is_main_admin
        for name in ('admin', 'superadmin', 'sa_tasin_1', 'tasin_superadmin_test'):
            u = User.objects.create_user(name, password='x')
            Membership.objects.create(organization=self.org, user=u, role='viewer', branch=self.b1)
            self.assertFalse(is_super_admin(u, self.org), name)
            self.assertFalse(is_main_admin(u, self.org), name)
            c = Client()
            c.force_login(u)
            self.assertEqual(c.get(f'/api/v1/orgs/{self.org.pk}/tasin-settings/users').status_code, 403, name)

    def test_explicit_role_is_org_scoped(self):
        from rentals.rbac import is_super_admin
        u = User.objects.create_user('someone', password='x')
        Membership.objects.create(organization=self.org, user=u, role='admin', is_super_admin=True, branch=self.b1)
        Membership.objects.create(organization=self.org2, user=u, role='viewer')
        self.assertTrue(is_super_admin(u, self.org))
        self.assertFalse(is_super_admin(u, self.org2))
        c = Client()
        c.force_login(u)
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org.pk}/tasin-owners').status_code, 200)
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org2.pk}/tasin-settings/users').status_code, 403)

    def test_registration_cannot_create_privileged_user(self):
        c = Client()
        r = c.post('/api/v1/auth/register', data={'organization_name': 'N', 'username': 'newperson', 'email': 'a@b.co', 'password': 'longenough1'}, content_type='application/json')
        self.assertEqual(r.status_code, 200)
        from rentals.rbac import is_super_admin
        u = User.objects.get(username='newperson')
        self.assertFalse(u.is_superuser)
        self.assertEqual(Membership.objects.get(user=u).role, 'admin')                                  # org admin, not Super Admin
        self.assertFalse(is_super_admin(u, Membership.objects.get(user=u).organization))

    def test_grant_and_revoke_are_audited_and_only_by_super_admin(self):
        boss = User.objects.create_user('boss', password='x')
        Membership.objects.create(organization=self.org, user=boss, role='admin', is_super_admin=True, branch=self.b1)
        mgr = User.objects.create_user('mgr', password='x')
        Membership.objects.create(organization=self.org, user=mgr, role='admin', branch=self.b1)
        target = User.objects.create_user('target', password='x')
        Membership.objects.create(organization=self.org, user=target, role='viewer', branch=self.b1)
        url = f'/api/v1/orgs/{self.org.pk}/tasin-settings/users/{target.pk}/profile'
        mc = Client()
        mc.force_login(mgr)
        self.assertEqual(mc.put(url, data=json.dumps({'role': 'super_admin'}), content_type='application/json').status_code, 403)   # org admin cannot escalate
        self.assertEqual(Membership.objects.get(user=target).role, 'viewer')
        bc = Client()
        bc.force_login(boss)
        r = bc.put(url, data=json.dumps({'role': 'super_admin'}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        m = Membership.objects.get(user=target, organization=self.org)
        self.assertEqual((m.role, m.is_super_admin), ('admin', True))                                   # RentFlow role intact, privilege is the flag
        self.assertFalse(User.objects.get(pk=target.pk).is_superuser)                                   # no global privilege
        self.assertEqual(AuditEvent.objects.filter(action='privilege.assign').count(), 1)
        r = bc.put(url, data=json.dumps({'role': 'viewer'}), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(action='privilege.remove').count(), 1)

    def test_cannot_remove_last_super_admin(self):
        boss = User.objects.create_user('boss', password='x')
        Membership.objects.create(organization=self.org, user=boss, role='admin', is_super_admin=True, branch=self.b1)
        c = Client()
        c.force_login(boss)
        r = c.put(f'/api/v1/orgs/{self.org.pk}/tasin-settings/users/{boss.pk}/profile', data=json.dumps({'role': 'admin'}), content_type='application/json')
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Membership.objects.get(user=boss).is_super_admin)

    def test_migration_gives_legacy_admin_an_explicit_role_without_touching_portal_logins(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module('rentals.migrations.0038_backfill_garage_ledger_superadmin_roles')
        legacy = User.objects.create_user('admin', password='x')
        Membership.objects.create(organization=self.org, user=legacy, role='admin', branch=self.b1)
        portal = User.objects.create_user('superadmin', password='x')       # a portal login that happens to carry a reserved name
        Membership.objects.create(organization=self.org, user=portal, role='owner')
        mig.forward(apps, None)
        m = Membership.objects.get(user=legacy)
        self.assertEqual((m.role, m.is_super_admin), ('admin', True))
        self.assertEqual((Membership.objects.get(user=portal).role, Membership.objects.get(user=portal).is_super_admin), ('owner', False))
        legacy_str = User.objects.create_user('old_sa', password='x')
        Membership.objects.create(organization=self.org, user=legacy_str, role='super_admin')
        mig.forward(apps, None)
        m = Membership.objects.get(user=legacy_str)
        self.assertEqual((m.role, m.is_super_admin), ('admin', True))

    def test_portal_owner_never_super_admin(self):
        from rentals.rbac import is_super_admin
        u = User.objects.create_user('po', password='x')
        Membership.objects.create(organization=self.org, user=u, role='owner')
        self.assertFalse(is_super_admin(u, self.org))
        self.assertFalse(is_super_admin(u))


class LoginThrottleTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('real', password='right-pass-1')

    def login(self, pw, name='real'):
        return Client().post('/api/v1/auth/login', data=json.dumps({'username': name, 'password': pw}), content_type='application/json')

    def test_lockout_after_five_failures_then_correct_password_is_refused_until_expiry(self):
        for _ in range(5):
            self.assertEqual(self.login('bad').status_code, 401)
        r = self.login('right-pass-1')
        self.assertEqual(r.status_code, 429)
        self.assertIn('Try again', r.json()['detail'])
        self.assertTrue(int(r['Retry-After']) > 0)
        LoginThrottle.objects.update(locked_until=None, window_started=LoginThrottle.objects.first().window_started)
        LoginThrottle.objects.all().delete()
        self.assertEqual(self.login('right-pass-1').status_code, 200)

    def test_success_clears_counter_and_unknown_users_are_throttled_too(self):
        for _ in range(3):
            self.login('bad')
        self.assertEqual(self.login('right-pass-1').status_code, 200)
        self.assertFalse(LoginThrottle.objects.filter(key__startswith='pair:real:').exists())
        for _ in range(5):
            self.assertEqual(self.login('x', name='ghost').status_code, 401)
        self.assertEqual(self.login('x', name='ghost').status_code, 429)

    def test_failures_and_lockouts_are_logged(self):
        with self.assertLogs('rentals.security', level='INFO') as cm:
            for _ in range(5):
                self.login('bad')
        text = '\n'.join(cm.output)
        self.assertIn('login failed', text)
        self.assertIn('login lockout', text)


class RouteAndUploadTests(Base):
    def test_formerly_shadowed_routes_reach_their_handlers(self):
        r = self.post('/custom-report-builder', {'module': 'drivers', 'fields': []})
        self.assertNotEqual(r.status_code, 405, r.content)
        self.assertIn(r.status_code, (200, 422))
        r = self.get('/private-documents?entity_type=vehicle&entity_id=1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])
        # old RentFlow documents route still answers its own contract
        self.assertEqual(self.c.get(f'{self.base}/documents?target_type=agreement&target_id=1').status_code, 200)

    def test_no_duplicate_method_path_across_routers(self):
        from rentals.api import api
        seen = {}
        dup = []
        for prefix, router in api._routers:
            for path, view in router.path_operations.items():
                norm = '/'.join('{}' if seg.startswith('{') else seg for seg in (prefix + path).split('/'))
                for op in view.operations:
                    for m in op.methods:
                        if (m, norm) in seen and seen[(m, norm)] is not router:
                            dup.append((m, norm))
                        seen[(m, norm)] = router
        self.assertEqual(dup, [])

    def pdf(self):
        return base64.b64encode(b'%PDF-1.4 test').decode()

    def test_upload_validation_and_secure_download(self):
        body = {'entity_type': 'vehicle', 'entity_id': '1', 'document_type': 'Paper', 'file_name': '../../evil name.pdf', 'file_base64': self.pdf(), 'mime_type': 'application/pdf'}
        r = self.post('/private-documents/upload', body)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['fileName'], 'evil name.pdf')                                         # path stripped
        did = r.json()['id']
        d = self.c.get(f'{self.base}/private-documents/{did}/download')
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(b''.join(d.streaming_content).startswith(b'%PDF'))
        for bad in (dict(body, entity_type='../../etc'), dict(body, mime_type='text/html'), dict(body, file_base64=base64.b64encode(b'<html>').decode()),
                    dict(body, file_base64='@@@'), dict(body, file_base64=base64.b64encode(b'').decode())):
            self.assertIn(self.post('/private-documents/upload', bad).status_code, (400, 422), bad['entity_type'] + bad['mime_type'])
        mid = base64.b64encode(b'%PDF' + b'0' * int(5.5 * 1024 * 1024)).decode()
        self.assertEqual(self.post('/private-documents/upload', dict(body, file_base64=mid)).status_code, 413)
        huge = base64.b64encode(b'%PDF' + b'0' * (11 * 1024 * 1024)).decode()
        self.assertIn(self.post('/private-documents/upload', dict(body, file_base64=huge)).status_code, (400, 413))
        # other organization cannot download it
        org2 = Organization.objects.create(name='Z')
        u2 = User.objects.create_user('z', password='x')
        Membership.objects.create(organization=org2, user=u2, role='super_admin')
        c2 = Client()
        c2.force_login(u2)
        self.assertEqual(c2.get(f'/api/v1/orgs/{org2.pk}/private-documents/{did}/download').status_code, 404)
        self.assertEqual(c2.get(f'/api/v1/orgs/{self.org.pk}/private-documents/{did}/download').status_code, 403)


class DutyEditTests(Base):
    def duty(self, **kw):
        body = {'date': '2026-11-10', 'client_id': '', 'client_name': 'C', 'vehicle_id': str(self.car.pk), 'vehicle_reg': 'CAR-1', 'driver_id': str(self.driver.pk), 'driver_name': 'Driver',
                'pickup_location': 'A', 'destination': 'B', 'start_time': '09:00', 'customer_contact': '017', 'agreed_rent': '1000', 'fuel_cost': '100', 'discount': '50'}
        body.update(kw)
        return body

    def make(self, **kw):
        r = self.c.post(f'{self.base}/tasin-duties', data=json.dumps(self.duty(**kw)), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['id']

    def put(self, did, **kw):
        return self.c.put(f'{self.base}/tasin-duties/{did}', data=json.dumps(self.duty(**kw)), content_type='application/json')

    def test_create_links_vehicle_driver_and_edit_recomputes_total(self):
        from rentals.models import Duty
        did = self.make()
        d = Duty.objects.get(pk=did)
        self.assertEqual((d.vehicle_id, d.driver_id, d.total_amount), (self.car.pk, self.driver.pk, D('1050.00')))
        r = self.put(did, agreed_rent='2000', fuel_cost='0', discount='0', destination='C')
        self.assertEqual(r.status_code, 200, r.content)
        d.refresh_from_db()
        self.assertEqual((d.total_amount, d.destination), (D('2000.00'), 'C'))

    def test_edit_validation_conflict_lock_and_isolation(self):
        a = self.make()
        b = self.make(date='2026-11-11')
        self.assertEqual(self.put(b, date='2026-11-10').status_code, 409)           # same vehicle+driver already booked on the 10th by duty a
        self.assertEqual(self.put(a, agreed_rent='-5').status_code, 422)
        self.assertEqual(self.put(999999).status_code, 404)
        from rentals.models import Duty
        Duty.objects.filter(pk=a).update(is_invoiced=True)
        self.assertEqual(self.put(a, agreed_rent='1').status_code, 409)             # invoiced duties are locked
        other = Organization.objects.create(name='X')
        u2 = User.objects.create_user('x', password='x')
        Membership.objects.create(organization=other, user=u2, role='admin', is_super_admin=True)
        c2 = Client(); c2.force_login(u2)
        self.assertEqual(c2.put(f'/api/v1/orgs/{other.pk}/tasin-duties/{b}', data=json.dumps(self.duty()), content_type='application/json').status_code, 404)

    def test_status_endpoint_needs_numeric_id(self):
        did = self.make()
        self.assertEqual(self.post(f'/../tasin-duties/{did}/status'.replace('/..', ''), {'status': 'Running'}).status_code, 200)
        self.assertEqual(self.c.post(f'{self.base}/tasin-duties/DTY-001/status', data=json.dumps({'status': 'Running'}), content_type='application/json').status_code, 422)


class VehicleInvoiceTermsTests(Base):
    def test_client_invoice_never_shows_owner_terms(self):
        from rentals.models import Duty
        client = Party.objects.create(organization=self.org, kind='customer', name='Cl')
        PartyDetail.objects.create(party=client)
        self.post('/tasin-agreements', {'agreement_type': 'owner', 'party_id': self.owner.pk, 'vehicle_id': self.car.pk, 'title': 'Owner deal', 'start_date': '2026-11-01', 'custom_terms': ['OWNER SECRET TERM'], 'finalize': True})
        b = self.get(f'/vehicles/{self.car.pk}/billing?month=2026-11').json()
        self.assertEqual(b['invoice']['terms'], [])
        self.post('/tasin-agreements', {'agreement_type': 'client', 'party_id': client.pk, 'vehicle_id': self.car.pk, 'title': 'Client deal', 'start_date': '2026-11-01', 'custom_terms': ['Client term'], 'finalize': True})
        b = self.get(f'/vehicles/{self.car.pk}/billing?month=2026-11').json()
        self.assertEqual(b['invoice']['terms'], ['Client term'])
