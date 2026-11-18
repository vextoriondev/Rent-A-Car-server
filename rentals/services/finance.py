"""Party ledger services: opening balances, driver salary/advance/fuel-gas, owner rent accrual, client receivables,
vehicle payments, daily report. Every money-changing function is atomic, locks the party row, and is idempotent
(unique keys + conditional unique constraints), so a repeated request can never double-post.

Sign convention (see PartyLedgerEntry.Account): the balance of an account is the sum of `delta`;
positive always means the outstanding amount the account name describes.
"""
import uuid
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.db import transaction, IntegrityError
from django.db.models import Sum, Q
from .helpers import DomainError
from .audit import audit
from ..models import (
    Party, Vehicle, PartyDetail, PartyLedgerEntry as E, SalaryPayment, RentAccrual, AccountTransaction, FinancialAccount,
    ReplacementAssignment, TasinInvoice, TasinInvoicePayment, FinanceSettings,
)

ZERO = Decimal('0.00')
CENT = Decimal('0.01')
A = E.Account
T = E.EntryType


class FinanceError(DomainError):
    """Business-rule violation with a plain-English message (HTTP 422, or the status the API maps from `status`)."""
    def __init__(self, code, message, status=422, **params):
        super().__init__(code, **params)
        self.message = message
        self.status = status

    def __str__(self):
        return self.message


# ─── helpers ────────────────────────────────────────────────────────────────

def money(value, allow_zero=False, field='amount'):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise FinanceError('invalid_amount', f'{field} must be a valid number')
    if amount != amount.quantize(CENT):
        raise FinanceError('invalid_amount', f'{field} can have at most 2 decimal places')
    if amount < 0 or (amount == 0 and not allow_zero):
        raise FinanceError('invalid_amount', f'{field} must be greater than zero' if not allow_zero else f'{field} cannot be negative')
    return amount.quantize(CENT)


def S(value):
    return str(Decimal(value).quantize(CENT))


def month_key(value):
    """Accepts 'YYYY-MM', 'YYYY-MM-DD' or a date; returns 'YYYY-MM'."""
    if isinstance(value, date):
        return f'{value.year:04d}-{value.month:02d}'
    s = str(value or '')[:7]
    try:
        y, m = int(s[:4]), int(s[5:7])
        assert 1 <= m <= 12 and s[4] == '-'
    except Exception:
        raise FinanceError('invalid_period', 'Month must look like YYYY-MM')
    return f'{y:04d}-{m:02d}'


def month_bounds(key):
    y, m = int(key[:4]), int(key[5:7])
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def new_txn_id(prefix='TXN'):
    return f'{prefix}-{uuid.uuid4().hex[:10].upper()}'


def org_of(org):
    return org


def lock_party(org, party_id, kind=None):
    qs = Party.objects.select_for_update().filter(pk=party_id, organization=org)
    if kind:
        qs = qs.filter(kind=kind)
    party = qs.first()
    if not party:
        raise FinanceError('party_not_found', 'Record not found', status=404)
    return party


def balance(party, account, org=None, until=None):
    qs = E.objects.filter(party=party, account=account)
    if until:
        qs = qs.filter(date__lte=until)
    return qs.aggregate(v=Sum('delta'))['v'] or ZERO


def _sync_counters(party):
    """Rewrite the legacy PartyDetail caches from the ledger (the ledger is authoritative)."""
    det, _ = PartyDetail.objects.get_or_create(party=party)

    def sums(account):
        agg = E.objects.filter(party=party, account=account).aggregate(
            pos=Sum('delta', filter=Q(delta__gt=0)), neg=Sum('delta', filter=Q(delta__lt=0)))
        return (agg['pos'] or ZERO), -(agg['neg'] or ZERO)

    if party.kind == 'owner':
        det.total_payable_owner, det.total_paid_owner = sums(A.OWNER_PAYABLE)
    elif party.kind == 'customer':
        det.total_receivable_client, det.total_collected_client = sums(A.CLIENT_RECEIVABLE)
    elif party.kind == 'driver':
        det.advance = balance(party, A.DRIVER_ADVANCE)
        paid = E.objects.filter(party=party, account=A.DRIVER_SALARY, entry_type=T.PAYMENT).aggregate(v=Sum('delta'))['v'] or ZERO
        legacy = E.objects.filter(party=party, account=A.DRIVER_SALARY, entry_type=T.LEGACY, delta__lt=0).aggregate(v=Sum('delta'))['v'] or ZERO
        det.total_paid_driver = -(paid + legacy)
    det.save()


def post_entry(org, party, account, entry_type, delta, on_date, user=None, unique_key=None, **extra):
    """Append one ledger row (+ refresh counters). With a unique_key that already exists, returns the existing row (replay)."""
    delta = Decimal(delta)
    if delta == 0:
        raise FinanceError('invalid_amount', 'Amount must not be zero')
    if unique_key:
        existing = E.objects.filter(organization=org, unique_key=unique_key).first()
        if existing:
            return existing, False
    try:
        with transaction.atomic():
            entry = E.objects.create(organization=org, party=party, account=account, entry_type=entry_type, delta=delta,
                                     date=on_date, unique_key=unique_key, created_by=user if getattr(user, 'pk', None) else None, **extra)
    except IntegrityError:
        existing = E.objects.filter(organization=org, unique_key=unique_key).first()
        if existing:
            return existing, False
        raise
    _sync_counters(party)
    return entry, True


def reverse_entry(org, entry, user, reason=''):
    rev, created = post_entry(org, entry.party, entry.account, T.REVERSAL, -entry.delta, date.today(), user,
                              unique_key=f'reversal:{entry.pk}', reverses=entry, period=entry.period, vehicle=entry.vehicle,
                              description=f'Reversal of #{entry.pk}: {reason}'[:500], source_type=entry.source_type, source_id=entry.source_id)
    return rev


def _post_account_txn(org, user, on_date, kind, category, person, amount, method, reference, description, vehicle_reg='', source='', key=None, custom_title='', account=None):
    return AccountTransaction.objects.create(
        organization=org, transaction_id=new_txn_id('TXN'), date=on_date, transaction_type=kind, category=category,
        related_person=person, vehicle_reg=vehicle_reg, amount=amount, payment_method=method or 'Cash',
        reference=reference or '', description=description or '', source=source, idempotency_key=key,
        custom_title=custom_title, created_by=user if getattr(user, 'pk', None) else None,
        to_account=account if kind == 'Income' else None, from_account=account if kind == 'Expense' else None)


def _entry_dict(e):
    return {'id': e.pk, 'date': e.date.isoformat(), 'account': e.account, 'type': e.entry_type, 'delta': S(e.delta),
            'period': e.period, 'vehicle': e.vehicle.registration if e.vehicle_id else '', 'method': e.payment_method,
            'reference': e.reference, 'description': e.description, 'reversed': hasattr(e, 'reversed_by') or e.entry_type == T.REVERSAL}


# ─── opening balances (all three party kinds) ──────────────────────────────

OPENING_TYPES = {
    'owner': {'payable': A.OWNER_PAYABLE, 'receivable': A.PARTY_OTHER_OWES_US, 'other': None},
    'customer': {'receivable': A.CLIENT_RECEIVABLE, 'payable': A.PARTY_OTHER_WE_OWE, 'other': None},
    'driver': {'salary_due': A.DRIVER_SALARY, 'advance': A.DRIVER_ADVANCE, 'fuel_gas': A.DRIVER_FUEL_GAS, 'other': None},
}


def set_opening_balance(org, party, user, balance_type, amount, on_date=None, direction=None, description='', reference='', attachment=''):
    """One opening balance per (party, account). Never creates an invoice/salary/expense: it is only a ledger row."""
    amount = money(amount, field='Opening amount')
    options = OPENING_TYPES.get(party.kind)
    if options is None or balance_type not in options:
        raise FinanceError('invalid_opening_type', f'Opening balance type "{balance_type}" is not valid for a {party.kind}')
    account = options[balance_type]
    if account is None:  # "other": the user states the direction explicitly
        if direction not in ('party_owes_us', 'we_owe_party'):
            raise FinanceError('direction_required', 'Choose whether the party owes us or we owe the party')
        account = A.PARTY_OTHER_OWES_US if direction == 'party_owes_us' else A.PARTY_OTHER_WE_OWE
    with transaction.atomic():
        lock_party(org, party.pk)
        key = f'opening:{party.pk}:{account}'
        if E.objects.filter(organization=org, unique_key=key).exists():
            raise FinanceError('opening_exists', 'An opening balance of this type already exists for this record. Post an adjustment instead.', status=409)
        entry, _ = post_entry(org, party, account, T.OPENING, amount, on_date or date.today(), user, unique_key=key,
                              description=description, reference=reference, attachment=attachment, source_type='opening')
        audit(org, user, 'opening_balance.set', 'party', party.pk, after={'account': account, 'amount': amount, 'date': entry.date})
    return entry


# ─── driver: salary, advance, fuel/gas ──────────────────────────────────────

def ensure_salary_accrual(org, driver, month, user=None):
    month = month_key(month)
    det, _ = PartyDetail.objects.get_or_create(party=driver)
    salary = det.monthly_salary or ZERO
    if salary <= 0:
        return None, False
    first, _ = month_bounds(month)
    return post_entry(org, driver, A.DRIVER_SALARY, T.ACCRUAL, salary, first, user, unique_key=f'salary:{driver.pk}:{month}',
                      period=month, description=f'Monthly salary {month}', source_type='salary')


def issue_advance(org, driver, user, amount, on_date, method='Cash', reference='', description='', key=None):
    amount = money(amount)
    with transaction.atomic():
        lock_party(org, driver.pk, 'driver')
        entry, created = post_entry(org, driver, A.DRIVER_ADVANCE, T.ISSUE, amount, on_date, user, unique_key=key and f'advance:{key}',
                                    payment_method=method, reference=reference, description=description or 'Advance issued', source_type='advance')
        if created:
            _post_account_txn(org, user, on_date, 'Expense', 'Driver Advance', driver.name, amount, method, reference,
                              f'Advance to {driver.name}: {description}', source='driver_advance', key=key and f'advance-txn:{key}')
            audit(org, user, 'driver.advance.issue', 'party', driver.pk, after={'amount': amount, 'date': on_date})
    return entry


def issue_fuel_gas(org, driver, user, amount, on_date, vehicle=None, reference='', description='', direction=None, method='Cash', key=None):
    """direction: 'driver_owes' = company gave the driver fuel/gas money (settled by returning/deducting);
    'company_owes' = driver paid fuel/gas from own pocket and is to be reimbursed. The two are never mixed."""
    amount = money(amount)
    direction = direction or get_finance_settings(org).fuel_default_direction
    if direction not in ('driver_owes', 'company_owes'):
        raise FinanceError('invalid_direction', 'Direction must be driver_owes or company_owes')
    account = A.DRIVER_FUEL_GAS if direction == 'driver_owes' else A.DRIVER_FUEL_REIMB
    with transaction.atomic():
        lock_party(org, driver.pk, 'driver')
        entry, created = post_entry(org, driver, account, T.ISSUE, amount, on_date, user, unique_key=key and f'fuel:{key}', vehicle=vehicle,
                                    payment_method=method, reference=reference, description=description or 'Fuel/Gas money', source_type='fuel_gas')
        if created and direction == 'driver_owes':
            _post_account_txn(org, user, on_date, 'Expense', 'Fuel/Gas Advance', driver.name, amount, method, reference,
                              f'Fuel/Gas money to {driver.name}: {description}', vehicle.registration if vehicle else '', 'fuel_gas', key and f'fuel-txn:{key}')
        audit(org, user, 'driver.fuel_gas.issue', 'party', driver.pk, after={'amount': amount, 'direction': direction})
    return entry


def pay_salary(org, driver_id, user, month, on_date, method='Cash', reference='', notes='', advance_adjustment=0,
               fuel_gas_settlement=0, fuel_gas_mode='none', fuel_gas_direction=None, amount_paid=None, idempotency_key=None):
    """Atomic salary settlement: accrues the month (once), offsets chosen advance / fuel-gas, pays cash, posts account transactions.
    Returns (SalaryPayment, replayed)."""
    month = month_key(month)
    adv = money(advance_adjustment, allow_zero=True, field='Advance adjustment')
    fuel = money(fuel_gas_settlement, allow_zero=True, field='Fuel/Gas settlement')
    policy = get_finance_settings(org_of(org))
    fuel_gas_direction = fuel_gas_direction or policy.fuel_default_direction
    if fuel_gas_mode == 'carry_forward':       # explicit: leave the fuel/gas balance outstanding, settle nothing now
        fuel, fuel_gas_mode = ZERO, 'none'
    if fuel_gas_mode not in {c.value for c in SalaryPayment.FuelMode}:
        raise FinanceError('invalid_fuel_mode', 'Invalid fuel/gas settlement mode')
    if fuel > 0 and fuel_gas_mode == 'none':
        raise FinanceError('fuel_mode_required', 'Choose how the fuel/gas amount is settled')
    if fuel == 0:
        fuel_gas_mode = 'none'
    fuel_acct = A.DRIVER_FUEL_GAS if fuel_gas_direction == 'driver_owes' else A.DRIVER_FUEL_REIMB
    if fuel_acct == A.DRIVER_FUEL_GAS and fuel_gas_mode == 'salary_addition':
        raise FinanceError('invalid_fuel_mode', 'Money the driver holds cannot be added to salary')
    if fuel_acct == A.DRIVER_FUEL_REIMB and fuel_gas_mode == 'salary_deduction':
        raise FinanceError('invalid_fuel_mode', 'A reimbursement owed to the driver cannot be deducted from salary')

    with transaction.atomic():
        driver = lock_party(org, driver_id, 'driver')
        if idempotency_key:
            prior = SalaryPayment.objects.filter(organization=org, idempotency_key=idempotency_key).first()
            if prior:
                if prior.driver_id != driver.pk or prior.salary_month != month:
                    raise FinanceError('idempotency_conflict', 'Idempotency key was used for a different request', status=409)
                return prior, True
        if SalaryPayment.objects.filter(organization=org, driver=driver, salary_month=month, status='posted').exists():
            raise FinanceError('salary_already_paid', f'Salary for {month} has already been settled for this driver', status=409)

        ensure_salary_accrual(org, driver, month, user)
        salary_bal = balance(driver, A.DRIVER_SALARY)
        month_salary = (PartyDetail.objects.get(party=driver).monthly_salary or ZERO)
        adv_bal = balance(driver, A.DRIVER_ADVANCE)
        fuel_bal = balance(driver, fuel_acct)
        if salary_bal <= 0:
            raise FinanceError('no_salary_due', 'No salary is due for this driver (monthly salary is zero or already paid)')
        if adv > adv_bal:
            raise FinanceError('advance_exceeds_balance', f'Advance adjustment (৳{adv}) exceeds outstanding advance (৳{adv_bal})')
        if fuel > fuel_bal:
            raise FinanceError('fuel_exceeds_balance', f'Fuel/Gas settlement (৳{fuel}) exceeds outstanding balance (৳{fuel_bal})')
        deduction = adv + (fuel if fuel_gas_mode == 'salary_deduction' else ZERO)
        if deduction > salary_bal:
            raise FinanceError('adjustment_exceeds_salary', f'Total adjustments (৳{deduction}) exceed the salary payable (৳{salary_bal})')
        net = salary_bal - deduction + (fuel if fuel_gas_mode == 'salary_addition' else ZERO)
        paid = net if amount_paid in (None, '') else money(amount_paid, allow_zero=True, field='Amount paid')
        if paid > net:
            raise FinanceError('paid_exceeds_net', f'Amount paid (৳{paid}) exceeds net salary payable (৳{net})')
        remaining = net - paid

        sp = SalaryPayment.objects.create(
            organization=org, driver=driver, salary_month=month, monthly_salary=month_salary, previous_salary_due=salary_bal - month_salary if salary_bal > month_salary else ZERO,
            advance_balance_before=adv_bal, advance_adjusted=adv, fuel_gas_balance_before=fuel_bal, fuel_gas_account=fuel_acct,
            fuel_gas_settled=fuel, fuel_gas_mode=fuel_gas_mode, net_payable=net, amount_paid=paid, remaining_salary_due=remaining,
            payment_date=on_date, payment_method=method, reference=reference, notes=notes, idempotency_key=idempotency_key,
            created_by=user if getattr(user, 'pk', None) else None)
        sid = str(sp.pk)
        common = dict(period=month, source_type='salary_payment', source_id=sid, reference=reference, payment_method=method)
        if adv:
            post_entry(org, driver, A.DRIVER_ADVANCE, T.SETTLEMENT, -adv, on_date, user, unique_key=f'salpay:{sid}:adv', description=f'Advance adjusted from {month} salary', **common)
            post_entry(org, driver, A.DRIVER_SALARY, T.SETTLEMENT, -adv, on_date, user, unique_key=f'salpay:{sid}:advoff', description=f'Advance deducted from {month} salary', **common)
        if fuel:
            post_entry(org, driver, fuel_acct, T.SETTLEMENT, -fuel, on_date, user, unique_key=f'salpay:{sid}:fuel', description=f'Fuel/Gas settled ({fuel_gas_mode})', **common)
            if fuel_gas_mode == 'salary_deduction':
                post_entry(org, driver, A.DRIVER_SALARY, T.SETTLEMENT, -fuel, on_date, user, unique_key=f'salpay:{sid}:fueloff', description=f'Fuel/Gas deducted from {month} salary', **common)
            elif fuel_gas_mode == 'salary_addition':
                post_entry(org, driver, A.DRIVER_SALARY, T.ADJUSTMENT, fuel, on_date, user, unique_key=f'salpay:{sid}:fueladd', description=f'Fuel/Gas reimbursement added to {month} salary', **common)
        if paid:
            post_entry(org, driver, A.DRIVER_SALARY, T.PAYMENT, -paid, on_date, user, unique_key=f'salpay:{sid}:pay', description=f'Salary paid for {month}', **common)
            _post_account_txn(org, user, on_date, 'Expense', 'Driver Salary', driver.name, paid, method, reference,
                              f'Salary {month} to {driver.name} (advance adj. ৳{adv}, fuel/gas ৳{fuel}): {notes}', source='salary_payment', key=f'salpay-txn:{sid}:salary')
        if fuel and fuel_gas_mode == 'cash':
            if fuel_acct == A.DRIVER_FUEL_GAS:
                _post_account_txn(org, user, on_date, 'Income', 'Fuel/Gas Settlement', driver.name, fuel, method, reference,
                                  f'Fuel/Gas money returned by {driver.name}', source='salary_payment', key=f'salpay-txn:{sid}:fuelcash')
            else:
                _post_account_txn(org, user, on_date, 'Expense', 'Fuel/Gas Reimbursement', driver.name, fuel, method, reference,
                                  f'Fuel/Gas reimbursed to {driver.name}', source='salary_payment', key=f'salpay-txn:{sid}:fuelcash')
        audit(org, user, 'driver.salary.pay', 'salary_payment', sp.pk, after={'month': month, 'net': net, 'paid': paid, 'advance': adv, 'fuel': fuel})
        _sync_counters(driver)
    return sp, False


def reverse_salary_payment(org, payment_id, user, reason=''):
    with transaction.atomic():
        sp = SalaryPayment.objects.select_for_update().filter(pk=payment_id, organization=org).first()
        if not sp:
            raise FinanceError('not_found', 'Salary payment not found', status=404)
        if sp.status != 'posted':
            raise FinanceError('already_reversed', 'Salary payment was already reversed', status=409)
        lock_party(org, sp.driver_id)
        for entry in E.objects.filter(organization=org, source_type='salary_payment', source_id=str(sp.pk), reverses__isnull=True).exclude(entry_type=T.REVERSAL):
            reverse_entry(org, entry, user, reason)
        for t in AccountTransaction.objects.filter(organization=org, idempotency_key__startswith=f'salpay-txn:{sp.pk}:'):
            _post_account_txn(org, user, date.today(), 'Income' if t.transaction_type == 'Expense' else 'Expense', f'Reversal: {t.category}',
                              t.related_person, t.amount, t.payment_method, t.reference, f'Reversal of {t.transaction_id}: {reason}', source='salary_reversal')
        sp.status = 'reversed'
        sp.save(update_fields=['status'])
        audit(org, user, 'driver.salary.reverse', 'salary_payment', sp.pk, reason=reason)
    return sp


def driver_summary(org, driver, date_from=None, date_to=None):
    det, _ = PartyDetail.objects.get_or_create(party=driver)
    qs = E.objects.filter(party=driver)

    def agg(account, **flt):
        return qs.filter(account=account, **flt).aggregate(v=Sum('delta'))['v'] or ZERO

    salary_earned = qs.filter(account=A.DRIVER_SALARY, delta__gt=0).exclude(entry_type=T.REVERSAL).aggregate(v=Sum('delta'))['v'] or ZERO
    salary_paid = -(agg(A.DRIVER_SALARY, entry_type=T.PAYMENT))
    salary_balance = balance(driver, A.DRIVER_SALARY)
    prev_salary = agg(A.DRIVER_SALARY, entry_type=T.OPENING)
    adv_received = qs.filter(account=A.DRIVER_ADVANCE, delta__gt=0).aggregate(v=Sum('delta'))['v'] or ZERO
    adv_adjusted = -(qs.filter(account=A.DRIVER_ADVANCE, delta__lt=0).aggregate(v=Sum('delta'))['v'] or ZERO)
    fg_total = qs.filter(account=A.DRIVER_FUEL_GAS, delta__gt=0).aggregate(v=Sum('delta'))['v'] or ZERO
    fg_settled = -(qs.filter(account=A.DRIVER_FUEL_GAS, delta__lt=0).aggregate(v=Sum('delta'))['v'] or ZERO)
    reimb_total = qs.filter(account=A.DRIVER_FUEL_REIMB, delta__gt=0).aggregate(v=Sum('delta'))['v'] or ZERO
    reimb_settled = -(qs.filter(account=A.DRIVER_FUEL_REIMB, delta__lt=0).aggregate(v=Sum('delta'))['v'] or ZERO)
    other = balance(driver, A.PARTY_OTHER_OWES_US) - balance(driver, A.PARTY_OTHER_WE_OWE)
    hist = qs.select_related('vehicle')
    if date_from:
        hist = hist.filter(date__gte=date_from)
    if date_to:
        hist = hist.filter(date__lte=date_to)
    return {
        'monthlySalary': S(det.monthly_salary),
        'previousSalaryDue': S(prev_salary),
        'totalSalaryEarned': S(salary_earned),
        'totalSalaryPaid': S(salary_paid),
        'remainingSalaryDue': S(salary_balance),
        'totalAdvanceReceived': S(adv_received),
        'totalAdvanceAdjusted': S(adv_adjusted),
        'remainingAdvanceDue': S(balance(driver, A.DRIVER_ADVANCE)),
        'totalFuelGas': S(fg_total),
        'fuelGasSettled': S(fg_settled),
        'remainingFuelGas': S(balance(driver, A.DRIVER_FUEL_GAS)),
        'fuelGasReimbursableTotal': S(reimb_total),
        'fuelGasReimbursed': S(reimb_settled),
        'remainingFuelGasReimbursable': S(balance(driver, A.DRIVER_FUEL_REIMB)),
        'otherOutstanding': S(other),
        'fuelPolicy': {'direction': get_finance_settings(org).fuel_default_direction, 'settlement': get_finance_settings(org).fuel_default_settlement},
        'accruedPeriods': sorted(set(qs.filter(account=A.DRIVER_SALARY, entry_type=T.ACCRUAL).values_list('period', flat=True))),
        'history': [_entry_dict(e) for e in hist.order_by('-date', '-id')],
        'salaryPayments': [{'id': p.pk, 'month': p.salary_month, 'net': S(p.net_payable), 'paid': S(p.amount_paid), 'advance': S(p.advance_adjusted),
                            'fuelGas': S(p.fuel_gas_settled), 'remaining': S(p.remaining_salary_due), 'date': p.payment_date.isoformat(),
                            'status': p.status, 'method': p.payment_method, 'reference': p.reference}
                           for p in SalaryPayment.objects.filter(driver=driver).order_by('-salary_month', '-id')],
    }


def driver_payable_for_dues(driver):
    """What the company owes the driver for Due & Collection: ledger salary balance plus the current month if not yet accrued."""
    det = getattr(driver, 'detail', None)
    bal = balance(driver, A.DRIVER_SALARY)
    if det and det.status == 'Active' and det.monthly_salary and det.monthly_salary > 0:
        key = month_key(date.today())
        if not E.objects.filter(organization=driver.organization, unique_key=f'salary:{driver.pk}:{key}').exists():
            bal += det.monthly_salary
    return bal


# ─── owner: rent accrual, payments, summary ─────────────────────────────────

def get_finance_settings(org):
    return FinanceSettings.objects.get_or_create(organization=org)[0]


PRORATION = {c.value for c in FinanceSettings.Proration}


def update_finance_settings(org, user, rent_proration=None, fuel_default_direction=None, fuel_default_settlement=None):
    """Change the accounting policy. Already-posted accruals and settlements are never recalculated; every change is audited."""
    fs = get_finance_settings(org)
    before = {'rent_proration': fs.rent_proration, 'fuel_default_direction': fs.fuel_default_direction, 'fuel_default_settlement': fs.fuel_default_settlement}
    if rent_proration is not None:
        if rent_proration not in PRORATION:
            raise FinanceError('invalid_setting', 'Unknown rent proration method')
        fs.rent_proration = rent_proration
    if fuel_default_direction is not None:
        if fuel_default_direction not in ('driver_owes', 'company_owes'):
            raise FinanceError('invalid_setting', 'Unknown fuel/gas direction')
        fs.fuel_default_direction = fuel_default_direction
    if fuel_default_settlement is not None:
        if fuel_default_settlement not in ('salary_deduction', 'cash', 'carry_forward'):
            raise FinanceError('invalid_setting', 'Unknown fuel/gas settlement method')
        fs.fuel_default_settlement = fuel_default_settlement
    fs.updated_by = user if getattr(user, 'pk', None) else None
    fs.save()
    after = {'rent_proration': fs.rent_proration, 'fuel_default_direction': fs.fuel_default_direction, 'fuel_default_settlement': fs.fuel_default_settlement}
    if before != after:
        audit(org, user, 'finance.settings.update', 'finance_settings', fs.pk, before=before, after=after)
    return fs


def _money_round(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def prorate_rent(rate, start, end, period, method):
    """Rent for one month. `start`/`end` are the inclusive assignment dates (None = open). Returns None when the assignment does not touch
    the month, else {amount, days_charged, days_in_basis, basis, note}. Pure function: Decimal arithmetic, rounded half-up to 0.01."""
    first, last = month_bounds(month_key(period))
    rate = Decimal(rate)
    eff_start = max(start, first) if start else first
    eff_end = min(end, last) if end else last
    if eff_end < eff_start:
        return None
    days = (eff_end - eff_start).days + 1          # inclusive: start day and return day are both charged
    dim = (last - first).days + 1
    if method == FinanceSettings.Proration.FULL_MONTH:
        amount, basis_days, note = rate, dim, 'Full month rent'
    elif method == FinanceSettings.Proration.CALENDAR_DAYS:
        amount, basis_days = _money_round(rate * days / dim), dim
        note = f'{rate} x {days}/{dim} days'
    elif method == FinanceSettings.Proration.THIRTY_DAY:
        basis_days = 30
        charged = 30 if days >= dim else min(days, 30)   # a full calendar month always bills one whole month
        amount = _money_round(rate * charged / 30)
        note = f'{rate} x {charged}/30 days'
    else:
        raise FinanceError('invalid_setting', 'Unknown rent proration method')
    return {'amount': _money_round(amount), 'days_charged': days, 'days_in_basis': basis_days, 'basis': str(method), 'note': note}


def eligible_rent_lines(org, period, owner_id=None):
    """Vehicles whose agreement window overlaps the month, with the calculation breakdown under the CURRENT policy."""
    period = month_key(period)
    method = get_finance_settings(org).rent_proration
    qs = Vehicle.objects.filter(organization=org).select_related('owner', 'detail')
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    lines = []
    for v in qs:
        d = getattr(v, 'detail', None)
        if not d or d.status == 'Inactive' or not d.monthly_rent or d.monthly_rent <= 0:
            continue
        calc = prorate_rent(d.monthly_rent, d.agreement_start, d.agreement_end, period, method)
        if not calc or calc['amount'] <= 0:
            continue
        posted = RentAccrual.objects.filter(organization=org, owner=v.owner, vehicle=v, period=period, status='posted').first()
        lines.append({'vehicleId': v.pk, 'registration': v.registration, 'ownerId': v.owner_id, 'owner': v.owner.name,
                      'amount': S(calc['amount']), 'monthlyRate': S(d.monthly_rent), 'basis': calc['basis'], 'daysCharged': calc['days_charged'],
                      'daysInBasis': calc['days_in_basis'], 'calculation': calc['note'],
                      'alreadyPosted': bool(posted), 'accrualId': posted.pk if posted else None,
                      'postedAmount': S(posted.amount) if posted else None})
    return lines


def accrue_rent(org, user, period, owner_id=None, vehicle_ids=None):
    """Post the month's rent to each owner's payable. Safe to re-run: existing (owner, vehicle, month) rows are skipped."""
    period = month_key(period)
    first, _ = month_bounds(period)
    created, skipped = [], []
    for line in eligible_rent_lines(org, period, owner_id):
        if vehicle_ids and line['vehicleId'] not in vehicle_ids:
            continue
        if line['alreadyPosted']:
            skipped.append(line)
            continue
        with transaction.atomic():
            owner = lock_party(org, line['ownerId'], 'owner')
            vehicle = Vehicle.objects.get(pk=line['vehicleId'])
            if RentAccrual.objects.filter(organization=org, owner=owner, vehicle=vehicle, period=period, status='posted').exists():
                skipped.append(line)
                continue
            try:
                with transaction.atomic():
                    acc = RentAccrual.objects.create(organization=org, owner=owner, vehicle=vehicle, period=period, amount=Decimal(line['amount']),
                                                     monthly_rate=Decimal(line['monthlyRate']), proration_basis=line['basis'], days_charged=line['daysCharged'],
                                                     days_in_basis=line['daysInBasis'], created_by=user if getattr(user, 'pk', None) else None)
            except IntegrityError:
                skipped.append(line)
                continue
            post_entry(org, owner, A.OWNER_PAYABLE, T.ACCRUAL, acc.amount, first, user, unique_key=f'rent:{acc.pk}', period=period, vehicle=vehicle,
                       description=f'Monthly rent {period} – {vehicle.registration} ({line["calculation"]})', source_type='rent_accrual', source_id=str(acc.pk))
            audit(org, user, 'owner.rent.accrue', 'rent_accrual', acc.pk, after={'period': period, 'amount': acc.amount, 'vehicle': vehicle.registration})
            created.append({**line, 'accrualId': acc.pk, 'alreadyPosted': True})
    return {'period': period, 'created': created, 'skipped': skipped}


def reverse_rent_accrual(org, user, accrual_id, reason):
    if not (reason or '').strip():
        raise FinanceError('reason_required', 'A reason is required to reverse an accrual')
    with transaction.atomic():
        acc = RentAccrual.objects.select_for_update().filter(pk=accrual_id, organization=org).first()
        if not acc:
            raise FinanceError('not_found', 'Accrual not found', status=404)
        if acc.status != 'posted':
            raise FinanceError('already_reversed', 'Accrual already reversed', status=409)
        lock_party(org, acc.owner_id)
        entry = E.objects.filter(organization=org, unique_key=f'rent:{acc.pk}').first()
        if entry:
            reverse_entry(org, entry, user, reason)
        acc.status = 'reversed'
        acc.reversal_reason = reason[:240]
        acc.save(update_fields=['status', 'reversal_reason'])
        audit(org, user, 'owner.rent.reverse', 'rent_accrual', acc.pk, reason=reason)
    return acc


def party_payment(org, party_id, kind, user, amount, on_date, method='Cash', reference='', note='', key=None, vehicle=None):
    """Pay an owner (reduces payable) or collect from a client (reduces receivable). Rejects over-payment/collection."""
    amount = money(amount)
    account = A.OWNER_PAYABLE if kind == 'owner' else A.CLIENT_RECEIVABLE
    with transaction.atomic():
        party = lock_party(org, party_id, kind)
        if key:
            prior = E.objects.filter(organization=org, unique_key=f'{kind}pay:{key}').first()
            if prior:
                return prior, balance(party, account), True
        bal = balance(party, account)
        if amount > bal:
            what = 'payable' if kind == 'owner' else 'receivable'
            raise FinanceError('exceeds_balance', f'Amount (৳{amount}) exceeds the outstanding {what} (৳{bal})')
        entry, created = post_entry(org, party, account, T.PAYMENT, -amount, on_date, user, unique_key=key and f'{kind}pay:{key}',
                                    payment_method=method, reference=reference, description=note or ('Payment to owner' if kind == 'owner' else 'Payment received'),
                                    source_type=f'{kind}_payment', vehicle=vehicle)
        _post_account_txn(org, user, on_date, 'Expense' if kind == 'owner' else 'Income', 'Owner Payment' if kind == 'owner' else 'Customer Collection',
                          party.name, amount, method, reference, f'{"Payment to owner" if kind == "owner" else "Payment received from client"} {party.name}: {note}',
                          vehicle.registration if vehicle else '', f'{kind}_payment', key and f'{kind}pay-txn:{key}')
        audit(org, user, f'{kind}.payment', 'party', party.pk, after={'amount': amount, 'date': on_date})
        return entry, balance(party, account), False


def owner_summary(org, owner, date_from=None, date_to=None):
    qs = E.objects.filter(party=owner)
    payable = qs.filter(account=A.OWNER_PAYABLE)
    opening = payable.filter(entry_type__in=[T.OPENING, T.LEGACY], delta__gt=0).aggregate(v=Sum('delta'))['v'] or ZERO
    accrued = payable.filter(entry_type=T.ACCRUAL).aggregate(v=Sum('delta'))['v'] or ZERO
    payments = -(payable.filter(delta__lt=0, entry_type__in=[T.PAYMENT, T.LEGACY]).aggregate(v=Sum('delta'))['v'] or ZERO)
    adjustments = payable.filter(entry_type__in=[T.ADJUSTMENT, T.REVERSAL]).aggregate(v=Sum('delta'))['v'] or ZERO
    current = balance(owner, A.OWNER_PAYABLE)
    receivable = balance(owner, A.PARTY_OTHER_OWES_US)
    by_vehicle = {}
    for e in payable.filter(entry_type=T.ACCRUAL).select_related('vehicle'):
        k = e.vehicle.registration if e.vehicle_id else '—'
        by_vehicle[k] = by_vehicle.get(k, ZERO) + e.delta
    hist = qs.select_related('vehicle')
    if date_from:
        hist = hist.filter(date__gte=date_from)
    if date_to:
        hist = hist.filter(date__lte=date_to)
    monthly = {}
    for e in payable:
        m = e.period or month_key(e.date)
        row = monthly.setdefault(m, {'month': m, 'accrued': ZERO, 'paid': ZERO})
        if e.entry_type == T.ACCRUAL:
            row['accrued'] += e.delta
        elif e.delta < 0:
            row['paid'] += -e.delta
    return {
        'openingBalance': S(opening), 'totalAccruedRent': S(accrued), 'totalPayments': S(payments), 'totalAdjustments': S(adjustments),
        'currentPayable': S(max(current, ZERO)), 'currentReceivable': S(receivable + max(-current, ZERO)), 'outstandingBalance': S(current),
        'vehicleBreakdown': [{'vehicle': k, 'accrued': S(v)} for k, v in by_vehicle.items()],
        'monthlyStatement': [{'month': r['month'], 'accrued': S(r['accrued']), 'paid': S(r['paid'])} for r in sorted(monthly.values(), key=lambda r: r['month'])],
        'history': [_entry_dict(e) for e in hist.order_by('-date', '-id')],
        'accruals': [{'id': a.pk, 'period': a.period, 'vehicle': a.vehicle.registration, 'amount': S(a.amount), 'status': a.status}
                     for a in RentAccrual.objects.filter(owner=owner).select_related('vehicle')],
    }


def client_summary(org, client, date_from=None, date_to=None):
    qs = E.objects.filter(party=client)
    rec = qs.filter(account=A.CLIENT_RECEIVABLE)
    opening = rec.filter(entry_type__in=[T.OPENING, T.LEGACY], delta__gt=0).aggregate(v=Sum('delta'))['v'] or ZERO
    invoiced = rec.filter(entry_type=T.INVOICE).aggregate(v=Sum('delta'))['v'] or ZERO
    collected = -(rec.filter(delta__lt=0, entry_type__in=[T.PAYMENT, T.LEGACY]).aggregate(v=Sum('delta'))['v'] or ZERO)
    hist = qs
    if date_from:
        hist = hist.filter(date__gte=date_from)
    if date_to:
        hist = hist.filter(date__lte=date_to)
    return {
        'openingReceivable': S(opening), 'totalInvoiced': S(invoiced), 'totalCollected': S(collected),
        'outstandingReceivable': S(balance(client, A.CLIENT_RECEIVABLE)),
        'previousPayable': S(balance(client, A.PARTY_OTHER_WE_OWE)), 'otherReceivable': S(balance(client, A.PARTY_OTHER_OWES_US)),
        'history': [_entry_dict(e) for e in hist.order_by('-date', '-id')],
    }


# ─── vehicle payments (receive / make) ─────────────────────────────────────

def _vehicle_payment(org, user, direction, vehicle, amount, on_date, counterparty, method='Cash', reference='', description='',
                     invoice_id=None, party_id=None, key=None):
    """direction 'receive' -> Income, 'make' -> Expense. A receive against an invoice reduces that invoice (and the client receivable once)."""
    amount = money(amount)
    if direction not in ('receive', 'make'):
        raise FinanceError('invalid_direction', 'Direction must be receive or make')
    if not (reference or key):
        raise FinanceError('reference_required', 'A unique reference (or idempotency key) is required')
    ref = (reference or '').strip()
    with transaction.atomic():
        if key:
            prior = AccountTransaction.objects.filter(organization=org, idempotency_key=f'vehpay:{key}').first()
            if prior:
                return prior, True
        if ref and AccountTransaction.objects.filter(organization=org, reference=ref, source='vehicle_payment').exists():
            raise FinanceError('duplicate_reference', f'Payment reference "{ref}" has already been used', status=409)
        party = lock_party(org, party_id) if party_id else None
        if invoice_id:
            if direction != 'receive':
                raise FinanceError('invoice_direction', 'Only received payments can be applied to an invoice')
            inv = TasinInvoice.objects.select_for_update().filter(pk=invoice_id, organization=org).first()
            if not inv:
                raise FinanceError('invoice_not_found', 'Invoice not found', status=404)
            rem = inv.total_amount - inv.paid_amount
            if amount > rem:
                raise FinanceError('exceeds_invoice', f'Payment (৳{amount}) exceeds the invoice balance (৳{rem})')
            count = inv.payments.count() + 1
            TasinInvoicePayment.objects.create(invoice=inv, payment_id=f'PAY-{inv.invoice_number}-{count:02d}', date=on_date, amount=amount,
                                               payment_method=method, reference=ref, notes=description)
            inv.paid_amount += amount
            inv.status = 'Paid' if inv.paid_amount >= inv.total_amount else 'Partially Paid'
            inv.save()
            lock_party(org, inv.client_id)
            post_entry(org, inv.client, A.CLIENT_RECEIVABLE, T.PAYMENT, -amount, on_date, user, unique_key=f'invpay:{inv.pk}:{count}', vehicle=vehicle,
                       payment_method=method, reference=ref, description=f'Payment for {inv.invoice_number}', source_type='invoice_payment', source_id=str(inv.pk))
            counterparty = counterparty or inv.client_name
        txn = _post_account_txn(org, user, on_date, 'Income' if direction == 'receive' else 'Expense',
                                'Vehicle Payment Received' if direction == 'receive' else 'Vehicle Payment Made', counterparty, amount, method, ref,
                                description, vehicle.registration, 'vehicle_payment', f'vehpay:{key}' if key else None)
        audit(org, user, f'vehicle.payment.{direction}', 'vehicle', vehicle.pk, after={'amount': amount, 'reference': ref, 'invoice': invoice_id})
    return txn, False


# ─── daily report ───────────────────────────────────────────────────────────

def daily_report(org, day):
    """Cash-basis report from posted AccountTransactions on one date. Transfers move money between our own accounts and are excluded."""
    txns = AccountTransaction.objects.filter(organization=org, date=day).exclude(transaction_type='Transfer').order_by('id')
    income = ZERO
    expense = ZERO
    inc_cat, exp_cat, rows = {}, {}, []
    for t in txns:
        cat = t.custom_title or t.category if t.category == 'Custom' else t.category
        if t.transaction_type == 'Income':
            income += t.amount
            inc_cat[cat] = inc_cat.get(cat, ZERO) + t.amount
        else:
            expense += t.amount
            exp_cat[cat] = exp_cat.get(cat, ZERO) + t.amount
        rows.append({'id': t.pk, 'transactionId': t.transaction_id, 'type': t.transaction_type, 'category': cat, 'description': t.description,
                     'paymentMethod': t.payment_method, 'party': t.related_person, 'vehicle': t.vehicle_reg, 'reference': t.reference, 'amount': S(t.amount)})
    return {'date': day.isoformat(), 'totalIncome': S(income), 'totalExpense': S(expense), 'netBalance': S(income - expense),
            'transactionCount': len(rows), 'incomeByCategory': {k: S(v) for k, v in inc_cat.items()},
            'expenseByCategory': {k: S(v) for k, v in exp_cat.items()}, 'transactions': rows}


# ─── custom transaction ─────────────────────────────────────────────────────

def _custom_transaction(org, user, kind, title, amount, on_date, method='Cash', description='', party_name='', vehicle_reg='', reference='', key=None):
    amount = money(amount)
    title = ' '.join((title or '').split())
    if kind not in ('Income', 'Expense'):
        raise FinanceError('invalid_type', 'Transaction type must be Income or Expense')
    if not title:
        raise FinanceError('title_required', 'A custom title is required')
    if len(title) > 160:
        raise FinanceError('title_too_long', 'Custom title is too long')
    with transaction.atomic():
        if key:
            prior = AccountTransaction.objects.filter(organization=org, idempotency_key=f'custom:{key}').first()
            if prior:
                return prior, True
        if reference and AccountTransaction.objects.filter(organization=org, reference=reference.strip()).exists():
            raise FinanceError('duplicate_reference', f'Reference "{reference}" already exists', status=409)
        if vehicle_reg and not Vehicle.objects.filter(organization=org, registration=vehicle_reg).exists():
            raise FinanceError('vehicle_not_found', 'Related vehicle not found', status=404)
        txn = _post_account_txn(org, user, on_date, kind, 'Custom', party_name, amount, method, reference.strip(), description or title, vehicle_reg,
                                'custom', f'custom:{key}' if key else None, custom_title=title)
        audit(org, user, 'transaction.custom', 'account_transaction', txn.pk, after={'title': title, 'amount': amount, 'type': kind})
    return txn, False


# ─── replacement drivers ────────────────────────────────────────────────────

def record_replacement(org, user, on_date, original_id, replacement_id, vehicle_id, reason='', notes=''):
    if original_id == replacement_id:
        raise FinanceError('same_driver', 'The replacement driver cannot be the same as the regular driver')
    original = Party.objects.filter(pk=original_id, organization=org, kind='driver').first()
    replacement = Party.objects.filter(pk=replacement_id, organization=org, kind='driver').first()
    vehicle = Vehicle.objects.filter(pk=vehicle_id, organization=org).first()
    if not original or not replacement or not vehicle:
        raise FinanceError('not_found', 'Driver or vehicle not found', status=404)
    if ReplacementAssignment.objects.filter(organization=org, vehicle=vehicle, date=on_date).exists():
        raise FinanceError('duplicate_replacement', 'A replacement is already recorded for this vehicle on this date', status=409)
    if ReplacementAssignment.objects.filter(organization=org, original_driver=original, date=on_date).exists():
        raise FinanceError('duplicate_replacement', 'This driver already has a replacement recorded on this date', status=409)
    if ReplacementAssignment.objects.filter(organization=org, replacement_driver=replacement, date=on_date).exists():
        raise FinanceError('replacement_busy', 'The replacement driver is already covering another vehicle on this date', status=409)
    with transaction.atomic():
        r = ReplacementAssignment.objects.create(organization=org, date=on_date, original_driver=original, replacement_driver=replacement,
                                                 vehicle=vehicle, reason=reason, notes=notes, created_by=user if getattr(user, 'pk', None) else None)
        audit(org, user, 'attendance.replacement', 'replacement', r.pk, after={'date': on_date, 'original': original.name, 'replacement': replacement.name, 'vehicle': vehicle.registration})
    return r


# ─── vehicle billing history / monthly invoice ──────────────────────────────

def vehicle_billing(org, vehicle, month=None):
    """Billing history by month (duty charges vs vehicle payments received) and, for a chosen month, the printable invoice."""
    from ..models import Duty, TasinAgreement
    from django.db.models.functions import TruncMonth
    duties = Duty.objects.filter(organization=org, vehicle=vehicle).exclude(status='Cancelled')
    pays = AccountTransaction.objects.filter(organization=org, vehicle_reg=vehicle.registration, source='vehicle_payment')
    months = {}
    for d in duties:
        m = months.setdefault(month_key(d.date), {'month': month_key(d.date), 'charges': ZERO, 'received': ZERO, 'paid': ZERO, 'invoices': set()})
        m['charges'] += d.total_amount
        if d.invoice_id:
            m['invoices'].add(d.invoice.invoice_number)
    for t in pays:
        m = months.setdefault(month_key(t.date), {'month': month_key(t.date), 'charges': ZERO, 'received': ZERO, 'paid': ZERO, 'invoices': set()})
        if t.transaction_type == 'Income':
            m['received'] += t.amount
        else:
            m['paid'] += t.amount
    history = []
    running = ZERO
    for key in sorted(months):
        m = months[key]
        running += m['charges'] - m['received']
        history.append({'month': key, 'charges': S(m['charges']), 'received': S(m['received']), 'paid': S(m['paid']), 'outstanding': S(running),
                        'status': 'Paid' if m['charges'] > 0 and m['received'] >= m['charges'] else 'Partial' if m['received'] > 0 else ('Unbilled' if m['charges'] == 0 else 'Unpaid'),
                        'invoices': sorted(m['invoices'])})
    out = {'vehicle': {'id': vehicle.pk, 'registration': vehicle.registration, 'owner': vehicle.owner.name}, 'history': list(reversed(history))}
    if month:
        month = month_key(month)
        first, last = month_bounds(month)
        mduties = duties.filter(date__gte=first, date__lte=last).order_by('date')
        mpays = pays.filter(date__gte=first, date__lte=last).order_by('date', 'id')
        charges = sum((d.total_amount for d in mduties), ZERO)
        received = sum((t.amount for t in mpays if t.transaction_type == 'Income'), ZERO)
        made = sum((t.amount for t in mpays if t.transaction_type == 'Expense'), ZERO)
        agr = TasinAgreement.objects.filter(organization=org, vehicle=vehicle).order_by('-id').first()
        from .tasin import get_or_create_company_profile
        cp = get_or_create_company_profile(org)
        out['invoice'] = {
            'month': month, 'number': f'VINV-{vehicle.pk}-{month.replace("-", "")}', 'company': cp.company_name, 'companyAddress': cp.address, 'companyPhone': cp.phone,
            'lines': [{'date': d.date.isoformat(), 'description': f'{d.duty_type}: {d.pickup_location} → {d.destination}', 'client': d.client_name, 'amount': S(d.total_amount)} for d in mduties],
            'payments': [{'date': t.date.isoformat(), 'direction': 'Received' if t.transaction_type == 'Income' else 'Paid', 'party': t.related_person, 'method': t.payment_method, 'reference': t.reference, 'amount': S(t.amount)} for t in mpays],
            'charges': S(charges), 'received': S(received), 'paid': S(made), 'balanceDue': S(charges - received),
            'terms': (list(agr.default_terms) + list(agr.custom_terms)) if agr else [],
        }
    return out


def backfill_legacy_counters(org=None):
    """Mirror legacy PartyDetail counters into the ledger (idempotent; same rules as migration 0036). Run after seeding/importing
    parties that were written straight to PartyDetail, otherwise the first ledger post would overwrite their counters."""
    qs = PartyDetail.objects.select_related('party')
    if org:
        qs = qs.filter(party__organization=org)
    made = 0
    today = date.today()

    def post(det, account, delta, tag):
        nonlocal made
        if not delta:
            return
        _, created = E.objects.get_or_create(organization=det.party.organization, unique_key=f'legacy:{det.party_id}:{account}:{tag}', defaults=dict(
            party=det.party, account=account, entry_type=T.LEGACY, delta=delta, date=today, description='Balance carried over from the previous version of the system', source_type='migration'))
        made += int(created)

    for det in qs.iterator():
        k = det.party.kind
        if k == 'owner':
            post(det, A.OWNER_PAYABLE, det.total_payable_owner, 'credit'); post(det, A.OWNER_PAYABLE, -det.total_paid_owner, 'debit')
        elif k == 'customer':
            post(det, A.CLIENT_RECEIVABLE, det.total_receivable_client, 'credit'); post(det, A.CLIENT_RECEIVABLE, -det.total_collected_client, 'debit')
        elif k == 'driver':
            post(det, A.DRIVER_ADVANCE, det.advance, 'advance'); post(det, A.DRIVER_FUEL_GAS, (det.fuel_advance or ZERO) + (det.gas_advance or ZERO), 'fuelgas')
            post(det, A.DRIVER_SALARY, det.total_paid_driver, 'paid-credit'); post(det, A.DRIVER_SALARY, -det.total_paid_driver, 'paid-debit')
    return made


def reconcile_ledger(org=None):
    """Independent audit: recompute every party's balances from the raw ledger rows in plain Python (no aggregation shared with
    `_sync_counters`) and compare with the legacy caches. Also flags rows whose organization differs from their party's and
    reversals that do not exactly cancel their original. Returns a list of discrepancy strings (empty = consistent)."""
    problems = []
    rows = E.objects.select_related('party').order_by('id')
    if org:
        rows = rows.filter(organization=org)
    per = {}
    by_id = {}
    for e in rows.iterator():
        by_id[e.pk] = e
        if e.organization_id != e.party.organization_id:
            problems.append(f'entry {e.pk}: organization {e.organization_id} != party organization {e.party.organization_id}')
        bucket = per.setdefault(e.party_id, {})
        acc = bucket.setdefault(e.account, {'pos': ZERO, 'neg': ZERO, 'pay': ZERO, 'legacy_neg': ZERO})
        if e.delta > 0:
            acc['pos'] += e.delta
        else:
            acc['neg'] += -e.delta
        if e.entry_type == T.PAYMENT:
            acc['pay'] += -e.delta
        if e.entry_type == T.LEGACY and e.delta < 0:
            acc['legacy_neg'] += -e.delta
        if e.reverses_id:
            orig = by_id.get(e.reverses_id) or E.objects.filter(pk=e.reverses_id).first()
            if orig is None or orig.delta + e.delta != 0 or orig.party_id != e.party_id or orig.account != e.account:
                problems.append(f'entry {e.pk}: does not cancel entry {e.reverses_id}')
    details = PartyDetail.objects.select_related('party')
    if org:
        details = details.filter(party__organization=org)
    for det in details.iterator():
        b = per.get(det.party_id)
        if b is None:
            continue  # never touched by the ledger: legacy counters are authoritative until backfilled
        kind = det.party.kind
        z = {'pos': ZERO, 'neg': ZERO, 'pay': ZERO, 'legacy_neg': ZERO}
        if kind == 'owner':
            a = b.get(A.OWNER_PAYABLE, z)
            exp = {'total_payable_owner': a['pos'], 'total_paid_owner': a['neg']}
        elif kind == 'customer':
            a = b.get(A.CLIENT_RECEIVABLE, z)
            exp = {'total_receivable_client': a['pos'], 'total_collected_client': a['neg']}
        elif kind == 'driver':
            adv = b.get(A.DRIVER_ADVANCE, z)
            sal = b.get(A.DRIVER_SALARY, z)
            exp = {'advance': adv['pos'] - adv['neg'], 'total_paid_driver': sal['pay'] + sal['legacy_neg']}
        else:
            continue
        for field, want in exp.items():
            have = getattr(det, field)
            if Decimal(have) != Decimal(want):
                problems.append(f'{kind} {det.party_id} {det.party.name}: {field} cache {have} != ledger {want}')
    for pid, accounts in per.items():
        for account, a in accounts.items():
            if account in (A.OWNER_PAYABLE, A.CLIENT_RECEIVABLE, A.DRIVER_ADVANCE, A.DRIVER_FUEL_GAS, A.DRIVER_FUEL_REIMB, A.DRIVER_SALARY) and a['pos'] - a['neg'] < 0:
                problems.append(f'party {pid} {account}: negative balance {a["pos"] - a["neg"]}')
    return problems


# ─── owner additional charges, issue reversal, expenses summary ─────────────

def owner_charge(org, owner_id, user, amount, on_date, description, vehicle=None, reference='', key=None):
    """An approved extra amount payable to the owner (not monthly rent). Idempotent by key."""
    amount = money(amount)
    if not (description or '').strip():
        raise FinanceError('description_required', 'Describe the charge')
    with transaction.atomic():
        owner = lock_party(org, owner_id, 'owner')
        entry, created = post_entry(org, owner, A.OWNER_PAYABLE, T.ACCRUAL, amount, on_date, user, unique_key=f'ocharge:{key}' if key else None,
                                    period=month_key(on_date), vehicle=vehicle, reference=reference, description=description.strip(), source_type='owner_charge')
        if created:
            audit(org, user, 'owner.charge', 'party', owner.pk, after={'amount': amount, 'description': description})
    return entry, not created


def reverse_issue_entry(org, entry_id, user, reason):
    """Reverse an advance / fuel-gas issue. Refused when that would leave the account negative (it was already settled)."""
    if not (reason or '').strip():
        raise FinanceError('reason_required', 'A reason is required')
    with transaction.atomic():
        e = E.objects.filter(pk=entry_id, organization=org).first()
        if not e or e.entry_type != T.ISSUE:
            raise FinanceError('not_found', 'Issue entry not found', status=404)
        lock_party(org, e.party_id)
        if E.objects.filter(reverses=e).exists():
            raise FinanceError('already_reversed', 'Already reversed', status=409)
        if balance(e.party, e.account) - e.delta < 0:
            raise FinanceError('already_settled', 'Part of this amount was already settled; reverse the settlement first', status=409)
        rev = reverse_entry(org, e, user, reason)
        txn = AccountTransaction.objects.filter(organization=org, idempotency_key__in=[f'advance-txn:{e.unique_key.split(":", 1)[1]}', f'fuel-txn:{e.unique_key.split(":", 1)[1]}']).first() if e.unique_key and ':' in e.unique_key else None
        if txn:
            _post_account_txn(org, user, date.today(), 'Income', f'Reversal: {txn.category}', txn.related_person, txn.amount, txn.payment_method, txn.reference,
                              f'Reversal of {txn.transaction_id}: {reason}', txn.vehicle_reg, 'issue_reversal')
        audit(org, user, 'driver.issue.reverse', 'party', e.party_id, reason=reason)
        return rev


def expense_summary(org, date_from=None, date_to=None, category=None, vehicle_reg=None):
    """Expense totals by category/vehicle from posted Expense transactions (reversal credits net out)."""
    qs = AccountTransaction.objects.filter(organization=org, transaction_type='Expense')
    cr = AccountTransaction.objects.filter(organization=org, transaction_type='Income', source__in=['garage_reversal', 'issue_reversal', 'salary_reversal'])
    for f in (qs, cr):
        pass
    if date_from:
        qs, cr = qs.filter(date__gte=date_from), cr.filter(date__gte=date_from)
    if date_to:
        qs, cr = qs.filter(date__lte=date_to), cr.filter(date__lte=date_to)
    if category:
        qs = qs.filter(category=category)
    if vehicle_reg:
        qs, cr = qs.filter(vehicle_reg=vehicle_reg), cr.filter(vehicle_reg=vehicle_reg)
    by_cat, by_veh, total = {}, {}, ZERO
    for t in qs:
        by_cat[t.category] = by_cat.get(t.category, ZERO) + t.amount
        if t.vehicle_reg:
            by_veh[t.vehicle_reg] = by_veh.get(t.vehicle_reg, ZERO) + t.amount
        total += t.amount
    credits = sum((t.amount for t in cr), ZERO)
    return {'total': S(total), 'reversalCredits': S(credits), 'net': S(total - credits), 'byCategory': {k: S(v) for k, v in by_cat.items()}, 'byVehicle': {k: S(v) for k, v in by_veh.items()}, 'count': qs.count()}


def vehicle_payment(*args, **kwargs):
    """See `_vehicle_payment`. A reference that loses a race is rejected by the database constraint and reported as 409 (everything rolled back)."""
    try:
        return _vehicle_payment(*args, **kwargs)
    except IntegrityError:
        ref = kwargs.get('reference') or (args[8] if len(args) > 8 else '')
        raise FinanceError('duplicate_reference', f'Payment reference "{ref}" has already been used', status=409)


def custom_transaction(*args, **kwargs):
    try:
        return _custom_transaction(*args, **kwargs)
    except IntegrityError:
        raise FinanceError('duplicate_reference', 'This reference already exists', status=409)
