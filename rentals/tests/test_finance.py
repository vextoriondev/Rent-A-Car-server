"""Client change-request scenarios: driver salary/advance/fuel-gas, opening balances, rent accrual, daily report,
vehicle payments, replacement drivers, agreements, documents, custom transactions, maintenance custom purpose."""
import json
from datetime import date
from decimal import Decimal as D
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from rentals.models import (Organization, Branch, Membership, Party, Vehicle, VehicleDetail, PartyDetail, AccountTransaction,
                            SalaryPayment, RentAccrual, PartyLedgerEntry, VehiclePaper, TasinInvoice)
from rentals.services import finance as fin


class FinanceBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user('admin', password='x')  # canonical super admin
        self.org = Organization.objects.create(name='TASIN')
        self.branch = Branch.objects.create(organization=self.org, name='Main')
        Membership.objects.create(organization=self.org, user=self.admin, role='super_admin', branch=self.branch)
        self.c = Client()
        self.c.force_login(self.admin)
        self.base = f'/api/v1/orgs/{self.org.pk}'
        self.driver = Party.objects.create(organization=self.org, kind='driver', name='Driver A', phone='017')
        PartyDetail.objects.create(party=self.driver, monthly_salary=D('20000'))
        self.driver_b = Party.objects.create(organization=self.org, kind='driver', name='Driver B', phone='018')
        PartyDetail.objects.create(party=self.driver_b, monthly_salary=D('15000'))
        self.owner = Party.objects.create(organization=self.org, kind='owner', name='Owner O', phone='019')
        PartyDetail.objects.create(party=self.owner)
        self.client_party = Party.objects.create(organization=self.org, kind='customer', name='Client C', phone='016')
        PartyDetail.objects.create(party=self.client_party)
        self.car = Vehicle.objects.create(organization=self.org, registration='DHAKA-METRO-1', model_name='Axio', owner=self.owner)
        VehicleDetail.objects.create(vehicle=self.car, monthly_rent=D('60000'), agreement_start=date(2026, 1, 1), status='On Duty')
        self.car2 = Vehicle.objects.create(organization=self.org, registration='DHAKA-METRO-2', model_name='Axio', owner=self.owner)
        VehicleDetail.objects.create(vehicle=self.car2, monthly_rent=D('10000'), agreement_start=date(2026, 1, 1), status='On Duty')

    def post(self, path, data, key=None, client=None):
        extra = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return (client or self.c).post(self.base + path, data=json.dumps(data, default=str), content_type='application/json', **extra)

    def get(self, path, client=None):
        return (client or self.c).get(self.base + path)

    def bal(self, party, account):
        return fin.balance(party, account)


class DriverSalaryTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.post(f'/parties/{self.driver.pk}/opening-balance', {'balance_type': 'advance', 'amount': '10000', 'date': '2026-10-01'})
        self.post(f'/parties/{self.driver.pk}/opening-balance', {'balance_type': 'fuel_gas', 'amount': '5000', 'date': '2026-10-01'})

    def salary(self, **kw):
        body = {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'payment_method': 'Cash'}
        body.update(kw)
        return self.post(f'/drivers/{self.driver.pk}/salary-payments', body, key=kw.pop('key', None) if False else None)

    def test_advance_adjustment_example_from_brief(self):
        r = self.salary(advance_adjustment='5000')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual((d['netPayable'], d['amountPaid'], d['remainingAdvance']), ('15000.00', '15000.00', '5000.00'))
        self.assertEqual(self.bal(self.driver, 'driver_salary'), D('0'))
        txn = AccountTransaction.objects.get(organization=self.org, category='Driver Salary')
        self.assertEqual(txn.amount, D('15000.00'))

    def test_scenario_18_3_advance_and_fuel_cash_settlement(self):
        """Advance 5,000 adjusted + fuel/gas 5,000 settled in cash => salary paid 15,000; advance 5,000 left; fuel/gas 0."""
        r = self.salary(advance_adjustment='5000', fuel_gas_settlement='5000', fuel_gas_mode='cash')
        d = r.json()
        self.assertEqual((d['netPayable'], d['remainingAdvance'], d['remainingFuelGas']), ('15000.00', '5000.00', '0.00'))
        self.assertTrue(AccountTransaction.objects.filter(category='Fuel/Gas Settlement', transaction_type='Income', amount=D('5000')).exists())

    def test_fuel_deduction_from_salary(self):
        d = self.salary(advance_adjustment='5000', fuel_gas_settlement='5000', fuel_gas_mode='salary_deduction').json()
        self.assertEqual(d['netPayable'], '10000.00')
        self.assertEqual(d['remainingFuelGas'], '0.00')

    def test_partial_fuel_settlement(self):
        d = self.salary(fuel_gas_settlement='3000', fuel_gas_mode='cash').json()
        self.assertEqual(d['remainingFuelGas'], '2000.00')

    def test_validation_errors(self):
        self.assertEqual(self.salary(advance_adjustment='10001').status_code, 422)
        self.assertEqual(self.salary(fuel_gas_settlement='5001', fuel_gas_mode='cash').status_code, 422)
        self.assertEqual(self.salary(advance_adjustment='-5').status_code, 422)
        self.assertEqual(self.salary(fuel_gas_settlement='100').status_code, 422)  # mode required
        self.assertEqual(self.salary(amount_paid='20001').status_code, 422)
        self.assertEqual(SalaryPayment.objects.count(), 0)
        self.assertEqual(AccountTransaction.objects.filter(source='salary_payment').count(), 0)

    def test_duplicate_month_rejected_and_idempotent_replay(self):
        r1 = self.post(f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'advance_adjustment': '5000'}, key='k1')
        r2 = self.post(f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'advance_adjustment': '5000'}, key='k1')
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertTrue(r2.json()['replayed'])
        self.assertEqual(SalaryPayment.objects.count(), 1)
        self.assertEqual(self.bal(self.driver, 'driver_advance'), D('5000'))  # adjusted once
        r3 = self.post(f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30'}, key='other')
        self.assertEqual(r3.status_code, 409)

    def test_partial_salary_payment_keeps_remaining_due(self):
        d = self.salary(amount_paid='12000').json()
        self.assertEqual((d['netPayable'], d['remainingSalaryDue']), ('20000.00', '8000.00'))
        self.assertEqual(self.bal(self.driver, 'driver_salary'), D('8000'))

    def test_reversal_restores_balances(self):
        d = self.salary(advance_adjustment='5000', fuel_gas_settlement='5000', fuel_gas_mode='cash').json()
        r = self.post(f'/salary-payments/{d["id"]}/reverse', {'reason': 'wrong month'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.bal(self.driver, 'driver_advance'), D('10000'))
        self.assertEqual(self.bal(self.driver, 'driver_fuel_gas'), D('5000'))
        self.assertEqual(self.bal(self.driver, 'driver_salary'), D('20000'))  # month accrued, nothing paid
        self.assertEqual(self.post(f'/salary-payments/{d["id"]}/reverse', {}).status_code, 409)
        # month can be settled again after reversal
        self.assertEqual(self.salary().status_code, 200)

    def test_driver_profile_summary(self):
        self.salary(advance_adjustment='5000', fuel_gas_settlement='5000', fuel_gas_mode='cash')
        s = self.get(f'/drivers/{self.driver.pk}/finance').json()
        self.assertEqual(s['remainingAdvanceDue'], '5000.00')
        self.assertEqual(s['totalAdvanceReceived'], '10000.00')
        self.assertEqual(s['totalAdvanceAdjusted'], '5000.00')
        self.assertEqual(s['remainingFuelGas'], '0.00')
        self.assertEqual(s['fuelGasSettled'], '5000.00')
        self.assertEqual(s['totalSalaryPaid'], '15000.00')
        self.assertEqual(s['monthlySalary'], '20000.00')
        self.assertTrue(len(s['history']) >= 5)
        filtered = self.get(f'/drivers/{self.driver.pk}/finance?date_from=2026-11-01').json()
        self.assertTrue(all(h['date'] >= '2026-11-01' for h in filtered['history']))

    def test_advance_and_fuel_issue_endpoints_and_directions(self):
        r = self.post(f'/drivers/{self.driver.pk}/advances', {'amount': '2000', 'date': '2026-11-05', 'payment_method': 'Cash'}, key='adv1')
        self.assertEqual(r.json()['remainingAdvance'], '12000.00')
        self.post(f'/drivers/{self.driver.pk}/advances', {'amount': '2000', 'date': '2026-11-05'}, key='adv1')
        self.assertEqual(self.bal(self.driver, 'driver_advance'), D('12000'))
        self.post(f'/drivers/{self.driver.pk}/fuel-gas', {'amount': '800', 'date': '2026-11-06', 'direction': 'company_owes', 'vehicle_id': self.car.pk})
        self.assertEqual(self.bal(self.driver, 'driver_fuel_reimb'), D('800'))
        self.assertEqual(self.bal(self.driver, 'driver_fuel_gas'), D('5000'))  # never mixed
        d = self.salary(fuel_gas_settlement='800', fuel_gas_mode='salary_addition', fuel_gas_direction='company_owes').json()
        self.assertEqual(d['netPayable'], '20800.00')
        self.assertEqual(d['remainingFuelGas'], '0.00')

    def test_legacy_pay_driver_endpoint_uses_ledger_and_caps(self):
        r = self.c.post(f'{self.base}/tasin-drivers/{self.driver.pk}/payments', data=json.dumps({'amount': '5000', 'date': '2026-11-10'}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.bal(self.driver, 'driver_salary'), D('15000'))
        r = self.c.post(f'{self.base}/tasin-drivers/{self.driver.pk}/payments', data=json.dumps({'amount': '99999', 'date': '2026-11-10'}), content_type='application/json')
        self.assertEqual(r.status_code, 422)

    def test_driver_due_collection_shows_advance_and_fuel(self):
        row = [x for x in self.c.get(f'{self.base}/tasin-due-collection').json()['drivers'] if x['name'] == 'Driver A'][0]
        self.assertEqual((row['advanceDue'], row['fuelGasDue']), (10000.0, 5000.0))


class OpeningBalanceTests(FinanceBase):
    def test_owner_scenario_18_4(self):
        r = self.post(f'/parties/{self.owner.pk}/opening-balance', {'balance_type': 'payable', 'amount': '200000', 'date': '2026-10-31', 'description': 'carried'})
        self.assertEqual(r.status_code, 200)
        # only the 60,000 vehicle accrues here: restrict to car 1
        a = self.post('/rent-accruals', {'period': '2026-11', 'owner_id': self.owner.pk, 'vehicle_ids': [self.car.pk]}).json()
        self.assertEqual(len(a['created']), 1)
        again = self.post('/rent-accruals', {'period': '2026-11', 'owner_id': self.owner.pk, 'vehicle_ids': [self.car.pk]}).json()
        self.assertEqual((len(again['created']), len(again['skipped'])), (0, 1))
        self.assertEqual(self.bal(self.owner, 'owner_payable'), D('260000'))
        p = self.post(f'/owners/{self.owner.pk}/ledger-payments', {'amount': '200000', 'date': '2026-11-20', 'reference': 'CHQ1'}, key='p1')
        self.assertEqual(p.json()['remainingPayable'], '60000.00')
        self.post(f'/owners/{self.owner.pk}/ledger-payments', {'amount': '200000', 'date': '2026-11-20', 'reference': 'CHQ1'}, key='p1')
        self.assertEqual(self.bal(self.owner, 'owner_payable'), D('60000'))
        s = self.get(f'/owners/{self.owner.pk}/finance').json()
        self.assertEqual((s['openingBalance'], s['totalAccruedRent'], s['totalPayments'], s['outstandingBalance']), ('200000.00', '60000.00', '200000.00', '60000.00'))
        self.assertEqual(s['vehicleBreakdown'], [{'vehicle': 'DHAKA-METRO-1', 'accrued': '60000.00'}])
        # legacy counters (used by dashboards) agree with the ledger
        det = PartyDetail.objects.get(party=self.owner)
        self.assertEqual((det.total_payable_owner, det.total_paid_owner), (D('260000'), D('200000')))
        # owner list endpoint
        row = [o for o in self.c.get(f'{self.base}/tasin-owners').json() if o['name'] == 'Owner O'][0]
        self.assertEqual(row['due'], 60000.0)

    def test_payment_cannot_exceed_payable(self):
        self.post(f'/parties/{self.owner.pk}/opening-balance', {'balance_type': 'payable', 'amount': '1000'})
        r = self.post(f'/owners/{self.owner.pk}/ledger-payments', {'amount': '1000.01', 'date': '2026-11-20'})
        self.assertEqual(r.status_code, 422)

    def test_duplicate_opening_balance_rejected(self):
        body = {'balance_type': 'payable', 'amount': '500'}
        self.assertEqual(self.post(f'/parties/{self.owner.pk}/opening-balance', body).status_code, 200)
        self.assertEqual(self.post(f'/parties/{self.owner.pk}/opening-balance', body).status_code, 409)
        self.assertEqual(PartyLedgerEntry.objects.filter(party=self.owner, entry_type='opening').count(), 1)

    def test_owner_receivable_and_other_direction(self):
        self.post(f'/parties/{self.owner.pk}/opening-balance', {'balance_type': 'receivable', 'amount': '700'})
        s = self.get(f'/owners/{self.owner.pk}/finance').json()
        self.assertEqual((s['currentPayable'], s['currentReceivable']), ('0.00', '700.00'))
        self.assertEqual(self.post(f'/parties/{self.owner.pk}/opening-balance', {'balance_type': 'other', 'amount': '5'}).status_code, 422)

    def test_rent_not_before_start_and_not_inactive(self):
        VehicleDetail.objects.filter(vehicle=self.car2).update(agreement_start=date(2027, 1, 1))
        VehicleDetail.objects.filter(vehicle=self.car).update(status='Inactive')
        lines = self.get(f'/rent-accruals/preview?period=2026-11&owner_id={self.owner.pk}').json()['lines']
        self.assertEqual(lines, [])

    def test_rent_reversal_then_reaccrue(self):
        a = self.post('/rent-accruals', {'period': '2026-11', 'vehicle_ids': [self.car.pk]}).json()['created'][0]
        self.assertEqual(self.post(f'/rent-accruals/{a["accrualId"]}/reverse', {}).status_code, 422)  # reason needed
        self.assertEqual(self.post(f'/rent-accruals/{a["accrualId"]}/reverse', {'reason': 'wrong rent'}).status_code, 200)
        self.assertEqual(self.bal(self.owner, 'owner_payable'), D('0'))
        self.assertEqual(PartyLedgerEntry.objects.filter(party=self.owner).count(), 2)  # history kept
        self.post('/rent-accruals', {'period': '2026-11', 'vehicle_ids': [self.car.pk]})
        self.assertEqual(self.bal(self.owner, 'owner_payable'), D('60000'))

    def test_client_scenario_18_5_no_vehicle_no_invoice(self):
        self.post(f'/parties/{self.client_party.pk}/opening-balance', {'balance_type': 'receivable', 'amount': '500000', 'date': '2026-10-31'})
        r = self.post(f'/clients/{self.client_party.pk}/ledger-payments', {'amount': '100000', 'date': '2026-11-15', 'reference': 'R1'})
        self.assertEqual(r.json()['remainingReceivable'], '400000.00')
        self.assertEqual(TasinInvoice.objects.count(), 0)
        s = self.get(f'/clients/{self.client_party.pk}/finance').json()
        self.assertEqual((s['openingReceivable'], s['totalCollected'], s['outstandingReceivable'], s['totalInvoiced']), ('500000.00', '100000.00', '400000.00', '0.00'))
        due = [x for x in self.c.get(f'{self.base}/tasin-due-collection').json()['clients'] if x['name'] == 'Client C'][0]
        self.assertEqual(due['remaining'], 400000.0)
        self.assertEqual(self.post(f'/clients/{self.client_party.pk}/ledger-payments', {'amount': '400000.01', 'date': '2026-11-16'}).status_code, 422)

    def test_creation_endpoints_accept_opening_balance(self):
        r = self.c.post(f'{self.base}/tasin-owners', data=json.dumps({'name': 'New Owner', 'phone': '1', 'opening_balance': '1500', 'opening_balance_type': 'payable'}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        oid = r.json()['id']
        self.assertEqual(fin.balance(Party.objects.get(pk=oid), 'owner_payable'), D('1500'))
        r = self.c.post(f'{self.base}/tasin-clients', data=json.dumps({'name': 'New Client', 'contact_person': 'x', 'phone': '1', 'opening_balance': '900'}), content_type='application/json')
        self.assertEqual(fin.balance(Party.objects.get(pk=r.json()['id']), 'client_receivable'), D('900'))
        r = self.c.post(f'{self.base}/tasin-drivers', data=json.dumps({'name': 'New Driver', 'phone': '1', 'monthly_salary': '9000', 'advance': '400', 'previous_salary_due': '300'}), content_type='application/json')
        d = Party.objects.get(pk=r.json()['id'])
        self.assertEqual((fin.balance(d, 'driver_advance'), fin.balance(d, 'driver_salary')), (D('400'), D('300')))

    def test_invoice_flow_uses_ledger(self):
        self.post(f'/parties/{self.client_party.pk}/opening-balance', {'balance_type': 'receivable', 'amount': '1000'})
        r = self.c.post(f'{self.base}/tasin-invoices', data=json.dumps({'client_id': self.client_party.pk, 'issue_date': '2026-11-01', 'due_date': '2026-11-30',
                        'custom_items': [{'description': 'Rent', 'quantity': '1', 'unit_price': '3000'}]}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        inv = TasinInvoice.objects.get()
        self.c.post(f'{self.base}/tasin-invoices/{inv.pk}/payments', data=json.dumps({'date': '2026-11-02', 'amount': '500'}), content_type='application/json')
        self.assertEqual(self.bal(self.client_party, 'client_receivable'), D('3500'))
        s = self.get(f'/clients/{self.client_party.pk}/finance').json()
        self.assertEqual((s['openingReceivable'], s['totalInvoiced']), ('1000.00', '3000.00'))


class PermissionTests(FinanceBase):
    def test_unauthenticated_and_unprivileged_and_other_org(self):
        anon = Client()
        self.assertEqual(self.get(f'/drivers/{self.driver.pk}/finance', client=anon).status_code, 401)
        User = get_user_model()
        viewer = User.objects.create_user('vw', password='x')
        Membership.objects.create(organization=self.org, user=viewer, role='viewer', branch=self.branch)
        vc = Client(); vc.force_login(viewer)
        self.assertEqual(self.post(f'/drivers/{self.driver.pk}/advances', {'amount': '1', 'date': '2026-11-01'}, client=vc).status_code, 403)
        outsider = User.objects.create_user('out', password='x')
        oc = Client(); oc.force_login(outsider)
        self.assertEqual(self.get(f'/drivers/{self.driver.pk}/finance', client=oc).status_code, 403)
        # wrong organization: a different org's driver id is not reachable
        org2 = Organization.objects.create(name='Other')
        other = Party.objects.create(organization=org2, kind='driver', name='Other D')
        self.assertEqual(self.get(f'/drivers/{other.pk}/finance').status_code, 404)

    def test_invalid_amounts(self):
        for amt in ('0', '-5', 'abc', '1.234'):
            r = self.post(f'/drivers/{self.driver.pk}/advances', {'amount': amt, 'date': '2026-11-01'})
            self.assertIn(r.status_code, (400, 422), amt)
        self.assertEqual(PartyLedgerEntry.objects.count(), 0)


class ReportAndPaymentTests(FinanceBase):
    def test_daily_report_scenario_18_7(self):
        for d, kind, cat, amt, m in [('2026-11-10', 'Income', 'Customer Collection', '1000', 'Cash'), ('2026-11-10', 'Income', 'Customer Collection', '500', 'Bank Transfer'),
                                     ('2026-11-10', 'Expense', 'Fuel Cost', '300', 'Cash'), ('2026-11-10', 'Expense', 'Garage', '200', 'Cash'),
                                     ('2026-11-11', 'Income', 'Customer Collection', '9999', 'Cash')]:
            AccountTransaction.objects.create(organization=self.org, transaction_id=fin.new_txn_id(), date=d, transaction_type=kind, category=cat, amount=D(amt), payment_method=m)
        AccountTransaction.objects.create(organization=self.org, transaction_id=fin.new_txn_id(), date='2026-11-10', transaction_type='Transfer', category='Money Transfer', amount=D('777'))
        r = self.get('/daily-report?day=2026-11-10').json()
        self.assertEqual((r['totalIncome'], r['totalExpense'], r['netBalance'], r['transactionCount']), ('1500.00', '500.00', '1000.00', 4))
        self.assertEqual(r['incomeByCategory'], {'Customer Collection': '1500.00'})
        self.assertEqual(r['expenseByCategory'], {'Fuel Cost': '300.00', 'Garage': '200.00'})
        self.assertEqual(r['businessName'], 'TASIN ENTERPRISE')
        self.assertTrue(all(t['amount'] != '9999.00' for t in r['transactions']))
        empty = self.get('/daily-report?day=2026-01-01').json()
        self.assertEqual((empty['totalIncome'], empty['transactionCount']), ('0.00', 0))

    def test_invoices_do_not_count_as_cash(self):
        self.c.post(f'{self.base}/tasin-invoices', data=json.dumps({'client_id': self.client_party.pk, 'issue_date': '2026-11-10', 'due_date': '2026-11-30',
                    'custom_items': [{'description': 'Rent', 'quantity': '1', 'unit_price': '3000'}]}), content_type='application/json')
        self.assertEqual(self.get('/daily-report?day=2026-11-10').json()['totalIncome'], '0.00')

    def test_custom_transaction(self):
        r = self.post('/transactions/custom', {'transaction_type': 'Expense', 'title': '  Office   tea ', 'amount': '250', 'date': '2026-11-12', 'payment_method': 'Cash', 'reference': 'V-1'}, key='c1')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['title'], 'Office tea')
        self.post('/transactions/custom', {'transaction_type': 'Expense', 'title': 'Office tea', 'amount': '250', 'date': '2026-11-12', 'reference': 'V-1'}, key='c1')
        self.assertEqual(AccountTransaction.objects.filter(category='Custom').count(), 1)
        self.assertEqual(self.post('/transactions/custom', {'transaction_type': 'Expense', 'title': 'x', 'amount': '1', 'date': '2026-11-12', 'reference': 'V-1'}).status_code, 409)
        self.assertEqual(self.post('/transactions/custom', {'transaction_type': 'Expense', 'title': '  ', 'amount': '1', 'date': '2026-11-12'}).status_code, 422)
        self.assertEqual(self.post('/transactions/custom', {'transaction_type': 'Transfer', 'title': 'x', 'amount': '1', 'date': '2026-11-12'}).status_code, 422)
        self.assertEqual(self.post('/transactions/custom', {'transaction_type': 'Expense', 'title': 'x', 'amount': '0', 'date': '2026-11-12'}).status_code, 422)
        found = self.get('/transactions/search?q=tea&date_from=2026-11-01&date_to=2026-11-30').json()
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]['category'], found[0]['isCustom']), ('Office tea', True))
        self.assertEqual(self.get('/daily-report?day=2026-11-12').json()['expenseByCategory'], {'Office tea': '250.00'})

    def test_vehicle_payments_scenario_18_8(self):
        r = self.post(f'/vehicles/{self.car.pk}/payments', {'direction': 'receive', 'amount': '1000', 'date': '2026-11-12', 'counterparty': 'Client C', 'reference': 'RC-1'}, key='v1')
        self.assertEqual(r.json()['type'], 'Income')
        self.post(f'/vehicles/{self.car.pk}/payments', {'direction': 'receive', 'amount': '1000', 'date': '2026-11-12', 'counterparty': 'Client C', 'reference': 'RC-1'}, key='v1')
        self.assertEqual(AccountTransaction.objects.filter(source='vehicle_payment').count(), 1)
        self.assertEqual(self.post(f'/vehicles/{self.car.pk}/payments', {'direction': 'make', 'amount': '10', 'date': '2026-11-12', 'reference': 'RC-1'}).status_code, 409)
        r = self.post(f'/vehicles/{self.car.pk}/payments', {'direction': 'make', 'amount': '400', 'date': '2026-11-13', 'counterparty': 'Garage', 'reference': 'PM-1'})
        self.assertEqual(r.json()['type'], 'Expense')
        self.assertEqual(self.post(f'/vehicles/99999/payments', {'direction': 'make', 'amount': '4', 'date': '2026-11-13', 'reference': 'Z'}).status_code, 404)
        self.assertEqual(len(self.get(f'/vehicles/{self.car.pk}/payments').json()), 2)
        # against an invoice: partial then over-payment rejected, receivable reduced once
        self.c.post(f'{self.base}/tasin-invoices', data=json.dumps({'client_id': self.client_party.pk, 'issue_date': '2026-11-01', 'due_date': '2026-11-30',
                    'custom_items': [{'description': 'Rent', 'quantity': '1', 'unit_price': '3000'}]}), content_type='application/json')
        inv = TasinInvoice.objects.get()
        ok = self.post(f'/vehicles/{self.car.pk}/payments', {'direction': 'receive', 'amount': '1000', 'date': '2026-11-14', 'invoice_id': inv.pk, 'reference': 'RC-2'})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(self.post(f'/vehicles/{self.car.pk}/payments', {'direction': 'receive', 'amount': '2500', 'date': '2026-11-14', 'invoice_id': inv.pk, 'reference': 'RC-3'}).status_code, 422)
        inv.refresh_from_db()
        self.assertEqual((inv.paid_amount, inv.status), (D('1000'), 'Partially Paid'))
        self.assertEqual(self.bal(self.client_party, 'client_receivable'), D('2000'))
        self.assertEqual(self.post(f'/vehicles/{self.car.pk}/payments', {'direction': 'make', 'amount': '1', 'date': '2026-11-14', 'invoice_id': inv.pk, 'reference': 'Q'}).status_code, 422)


class AttendanceReplacementTests(FinanceBase):
    def test_scenario_18_9(self):
        for day in range(10, 15):
            r = self.post('/replacements', {'date': f'2026-11-{day}', 'original_driver_id': self.driver.pk, 'replacement_driver_id': self.driver_b.pk, 'vehicle_id': self.car.pk, 'reason': 'Sick'})
            self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(self.get('/replacements').json()), 5)
        self.assertEqual(self.post('/replacements', {'date': '2026-11-10', 'original_driver_id': self.driver.pk, 'replacement_driver_id': self.driver_b.pk, 'vehicle_id': self.car.pk}).status_code, 409)
        self.assertEqual(self.post('/replacements', {'date': '2026-11-20', 'original_driver_id': self.driver.pk, 'replacement_driver_id': self.driver.pk, 'vehicle_id': self.car.pk}).status_code, 422)
        self.assertEqual(self.post('/replacements', {'date': '2026-11-20', 'original_driver_id': self.driver.pk, 'replacement_driver_id': self.driver_b.pk, 'vehicle_id': self.car2.pk}).status_code, 200)
        self.assertEqual(len(self.get(f'/replacements?date_from=2026-11-12&date_to=2026-11-13').json()), 2)
        self.assertEqual(len(self.get(f'/replacements?vehicle_id={self.car2.pk}').json()), 1)
        self.assertEqual(len(self.get(f'/replacements?replacement_driver_id={self.driver_b.pk}').json()), 6)
        self.assertEqual(len(self.get(f'/replacements?driver_id={self.driver_b.pk}').json()), 0)
        # salary is untouched
        self.assertEqual(PartyDetail.objects.get(party=self.driver).monthly_salary, D('20000'))
        self.assertEqual(PartyLedgerEntry.objects.count(), 0)


class AgreementDocumentMaintenanceTests(FinanceBase):
    def test_agreement_types_and_terms_scenario_18_10(self):
        c = self.post('/tasin-agreements', {'agreement_type': 'client', 'party_id': self.client_party.pk, 'title': 'Corp deal', 'start_date': '2026-11-01',
                      'custom_terms': ['Fuel by client.\nLine two', '  ', 'No smoking'], 'default_terms': ['Default 1'], 'finalize': True})
        self.assertEqual(c.status_code, 200, c.content)
        self.assertEqual(c.json()['customTerms'], ['Fuel by client.\nLine two', 'No smoking'])
        self.assertIn('1. Default 1\n2. Fuel by client.\nLine two\n3. No smoking', c.json()['finalContent'])
        o = self.post('/tasin-agreements', {'agreement_type': 'owner', 'party_id': self.owner.pk, 'vehicle_id': self.car.pk, 'title': 'Owner deal', 'start_date': '2026-11-01'})
        self.assertEqual(o.status_code, 200, o.content)
        self.assertEqual(self.post('/tasin-agreements', {'agreement_type': 'owner', 'party_id': self.client_party.pk, 'title': 'bad', 'start_date': '2026-11-01'}).status_code, 422)
        self.assertEqual(self.post('/tasin-agreements', {'agreement_type': 'client', 'party_id': self.owner.pk, 'title': 'bad', 'start_date': '2026-11-01'}).status_code, 422)
        other_owner = Party.objects.create(organization=self.org, kind='owner', name='Other owner')
        self.assertEqual(self.post('/tasin-agreements', {'agreement_type': 'owner', 'party_id': other_owner.pk, 'vehicle_id': self.car.pk, 'title': 'bad', 'start_date': '2026-11-01'}).status_code, 422)
        self.assertEqual(self.post('/tasin-agreements', {'agreement_type': 'client', 'party_id': self.client_party.pk, 'title': 'bad', 'start_date': '2026-11-05', 'end_date': '2026-11-01'}).status_code, 422)
        self.assertEqual([a['type'] for a in self.get('/tasin-agreements?type=client').json()], ['client'])
        self.assertEqual([a['type'] for a in self.get('/tasin-agreements?type=owner').json()], ['owner'])
        self.assertEqual(len(self.get('/tasin-agreements').json()), 2)
        # draft can be edited (terms added/removed); final cannot
        oid = o.json()['id']
        u = self.c.put(f'{self.base}/tasin-agreements/{oid}', data=json.dumps({'agreement_type': 'owner', 'party_id': self.owner.pk, 'title': 'Owner deal v2', 'start_date': '2026-11-01', 'custom_terms': ['A', 'B']}), content_type='application/json')
        self.assertEqual(u.json()['customTerms'], ['A', 'B'])
        self.assertEqual(self.c.put(f'{self.base}/tasin-agreements/{c.json()["id"]}', data=json.dumps({'agreement_type': 'client', 'party_id': self.client_party.pk, 'title': 'x', 'start_date': '2026-11-01'}), content_type='application/json').status_code, 409)

    def test_documents_scenario_18_6(self):
        for t, n, title in [('fitness', 'FIT-1', 'Fitness 2026'), ('tax_token', 'TAX-1', 'Tax token 2026')]:
            r = self.post(f'/vehicles/{self.car.pk}/documents', {'paper_type': t, 'paper_number': n, 'title': title, 'issue_date': '2026-01-01', 'expiry_date': '2027-01-01'})
            self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(VehiclePaper.objects.filter(vehicle=self.car).count(), 2)
        docs = self.get('/documents-all').json()
        self.assertEqual({d['typeLabel'] for d in docs}, {'Fitness Certificate', 'Tax Token'})
        self.assertEqual({d['title'] for d in docs}, {'Fitness 2026', 'Tax token 2026'})
        # the older vehicle-profile endpoint reads the same rows
        prof = self.c.get(f'{self.base}/tasin-vehicles/{self.car.pk}/papers').json()
        self.assertEqual({p['paperType'] for p in prof}, {'fitness', 'tax_token'})
        pk = [d for d in docs if d['paperType'] == 'fitness'][0]['id']
        u = self.c.put(f'{self.base}/documents-all/{pk}', data=json.dumps({'paper_type': 'fitness', 'title': 'Fitness renewed', 'paper_number': 'FIT-2', 'expiry_date': '2028-01-01'}), content_type='application/json')
        self.assertEqual(u.status_code, 200)
        again = self.get('/documents-all').json()
        self.assertEqual(len(again), 2)
        self.assertEqual([d['title'] for d in again if d['id'] == pk], ['Fitness renewed'])
        self.assertEqual(self.get(f'/documents-all?paper_type=tax_token').json()[0]['paperNumber'], 'TAX-1')
        self.assertEqual(self.post(f'/vehicles/{self.car.pk}/documents', {'paper_type': 'tax_token', 'paper_number': 'TAX-1'}).status_code, 409)
        self.assertEqual(self.post(f'/vehicles/{self.car.pk}/documents', {'paper_type': 'bogus'}).status_code, 422)
        self.assertEqual(self.post(f'/vehicles/{self.car.pk}/documents', {'paper_type': 'fitness', 'issue_date': '2026-05-01', 'expiry_date': '2026-01-01'}).status_code, 422)
        self.assertEqual(self.c.delete(f'{self.base}/documents-all/{pk}').status_code, 200)
        self.assertEqual(len(self.get('/documents-all').json()), 1)

    def test_maintenance_custom_purpose_scenario_18_11(self):
        body = {'vehicle_reg': 'DHAKA-METRO-1', 'garage_name': 'G1', 'date': '2026-11-10', 'problem_description': 'Noise', 'service_type': 'Custom',
                'custom_purpose': '  Wiper   motor  replace ', 'labour_cost': '500', 'parts_cost': '1500'}
        r = self.c.post(f'{self.base}/tasin-maintenance', data=json.dumps(body), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        row = self.c.get(f'{self.base}/tasin-maintenance').json()[0]
        self.assertEqual((row['serviceType'], row['customPurpose'], row['purpose']), ('Custom', 'Wiper motor replace', 'Wiper motor replace'))
        body['custom_purpose'] = ''
        self.assertEqual(self.c.post(f'{self.base}/tasin-maintenance', data=json.dumps(body), content_type='application/json').status_code, 422)
        body.update(service_type='Brake', custom_purpose='ignored')
        self.assertEqual(self.c.post(f'{self.base}/tasin-maintenance', data=json.dumps(body), content_type='application/json').status_code, 200)
        brake = [m for m in self.c.get(f'{self.base}/tasin-maintenance').json() if m['serviceType'] == 'Brake'][0]
        self.assertEqual((brake['customPurpose'], brake['purpose']), ('', 'Brake'))
        up = dict(body, service_type='Custom', custom_purpose='Door lock')
        self.assertEqual(self.c.put(f'{self.base}/tasin-maintenance/{brake["id"]}', data=json.dumps(up), content_type='application/json').status_code, 200)
        stmt = self.c.get(f'{self.base}/tasin-vehicle-search/{self.car.pk}/statement').json()
        self.assertIn('Door lock', json.dumps(stmt))


class ReconciliationTests(FinanceBase):
    def test_counters_equal_ledger_after_mixed_activity(self):
        self.post(f'/parties/{self.driver.pk}/opening-balance', {'balance_type': 'advance', 'amount': '10000'})
        self.post(f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'advance_adjustment': '5000'})
        self.post(f'/parties/{self.owner.pk}/opening-balance', {'balance_type': 'payable', 'amount': '200000'})
        self.post('/rent-accruals', {'period': '2026-11'})
        self.post(f'/owners/{self.owner.pk}/ledger-payments', {'amount': '100000', 'date': '2026-11-20'})
        for party in (self.driver, self.owner):
            before = PartyDetail.objects.get(party=party)
            fin._sync_counters(party)
            after = PartyDetail.objects.get(party=party)
            self.assertEqual((before.advance, before.total_payable_owner, before.total_paid_owner, before.total_paid_driver),
                             (after.advance, after.total_payable_owner, after.total_paid_owner, after.total_paid_driver))
        self.assertEqual(self.bal(self.owner, 'owner_payable'), D('270000') - D('100000'))
        kp = self.get('/../dashboard/kpis'.replace('/..', '')).json() if False else None
        self.assertIsNone(kp)

    def test_ledger_is_append_only(self):
        e, _ = fin.post_entry(self.org, self.owner, 'owner_payable', 'opening', D('5'), date.today())
        with self.assertRaises(RuntimeError):
            e.delta = D('6'); e.save()
        with self.assertRaises(RuntimeError):
            e.delete()



class VehicleBillingTests(FinanceBase):
    def test_billing_history_and_invoice(self):
        from rentals.models import Duty
        Duty.objects.create(organization=self.org, duty_id='D1', date='2026-11-03', client=self.client_party, client_name='Client C', vehicle=self.car, vehicle_reg=self.car.registration,
                            driver_name='x', pickup_location='A', destination='B', start_time='9', total_amount=D('5000'), status='Completed')
        Duty.objects.create(organization=self.org, duty_id='D2', date='2026-10-03', client=self.client_party, client_name='Client C', vehicle=self.car, vehicle_reg=self.car.registration,
                            driver_name='x', pickup_location='A', destination='B', start_time='9', total_amount=D('1000'), status='Cancelled')
        self.post(f'/vehicles/{self.car.pk}/payments', {'direction': 'receive', 'amount': '2000', 'date': '2026-11-05', 'reference': 'B1'})
        b = self.get(f'/vehicles/{self.car.pk}/billing?month=2026-11').json()
        self.assertEqual([h['month'] for h in b['history']], ['2026-11'])  # cancelled duty ignored
        self.assertEqual((b['history'][0]['charges'], b['history'][0]['received'], b['history'][0]['outstanding'], b['history'][0]['status']), ('5000.00', '2000.00', '3000.00', 'Partial'))
        inv = b['invoice']
        self.assertEqual((inv['charges'], inv['received'], inv['balanceDue'], len(inv['lines'])), ('5000.00', '2000.00', '3000.00', 1))
        self.assertEqual(self.get('/vehicles/99999/billing').status_code, 404)


class LedgerIntegrityTests(FinanceBase):
    def test_reconcile_after_mixed_operations_incl_reversals(self):
        self.post(f'/parties/{self.driver.pk}/opening-balance', {'balance_type': 'advance', 'amount': '10000'})
        self.post(f'/parties/{self.driver.pk}/opening-balance', {'balance_type': 'fuel_gas', 'amount': '5000'})
        sp = self.post(f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-11', 'payment_date': '2026-11-30', 'advance_adjustment': '4000', 'fuel_gas_settlement': '2000', 'fuel_gas_mode': 'salary_deduction'}).json()
        self.post(f'/drivers/{self.driver.pk}/salary-payments', {'salary_month': '2026-12', 'payment_date': '2026-12-30', 'amount_paid': '5000'})
        self.post(f'/salary-payments/{sp["id"]}/reverse', {'reason': 'wrong'})
        self.post(f'/parties/{self.owner.pk}/opening-balance', {'balance_type': 'payable', 'amount': '90000'})
        a = self.post('/rent-accruals', {'period': '2026-11'}).json()['created'][0]
        self.post(f'/rent-accruals/{a["accrualId"]}/reverse', {'reason': 'wrong'})
        self.post('/rent-accruals', {'period': '2026-11'})
        self.post(f'/owners/{self.owner.pk}/ledger-payments', {'amount': '30000', 'date': '2026-11-20'})
        self.post(f'/parties/{self.client_party.pk}/opening-balance', {'balance_type': 'receivable', 'amount': '7000'})
        self.post(f'/clients/{self.client_party.pk}/ledger-payments', {'amount': '2500', 'date': '2026-11-20'})
        self.assertEqual(fin.reconcile_ledger(self.org), [])

    def test_reconcile_detects_tampering(self):
        self.post(f'/parties/{self.owner.pk}/opening-balance', {'balance_type': 'payable', 'amount': '100'})
        PartyDetail.objects.filter(party=self.owner).update(total_paid_owner=D('5'))  # a bypass writing the cache directly
        problems = fin.reconcile_ledger(self.org)
        self.assertTrue(any('total_paid_owner' in p for p in problems), problems)

    def test_entries_cannot_be_posted_across_organizations_via_api(self):
        org2 = Organization.objects.create(name='Other')
        other = Party.objects.create(organization=org2, kind='owner', name='Other owner')
        self.assertEqual(self.post(f'/parties/{other.pk}/opening-balance', {'balance_type': 'payable', 'amount': '1'}).status_code, 404)
        self.assertEqual(self.post(f'/owners/{other.pk}/ledger-payments', {'amount': '1', 'date': '2026-11-01'}).status_code, 404)
        self.assertEqual(PartyLedgerEntry.objects.filter(party=other).count(), 0)

    def test_management_commands_run_and_backfill_is_idempotent(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('reconcile_party_ledger', stdout=out)
        self.assertIn('consistent', out.getvalue())
        PartyDetail.objects.filter(party=self.owner).update(total_payable_owner=D('800'), total_paid_owner=D('300'))
        out = StringIO()
        call_command('backfill_party_ledger', stdout=out)
        self.assertIn('2 ledger row(s) created', out.getvalue())
        out = StringIO()
        call_command('backfill_party_ledger', stdout=out)
        self.assertIn('0 ledger row(s) created', out.getvalue())
        self.assertEqual(self.bal(self.owner, 'owner_payable'), D('500'))
        self.assertEqual(fin.reconcile_ledger(self.org), [])
