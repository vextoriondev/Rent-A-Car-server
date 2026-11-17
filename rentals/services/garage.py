"""Garage / vendor payables on an append-only ledger. Bills come from maintenance work; payments reduce the payable and also post a
cash-basis Expense transaction. The legacy Garage.total_bill/total_paid are caches rewritten from the ledger."""
from datetime import date
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.db.models import Sum, Q
from .audit import audit
from .finance import FinanceError, money, S, ZERO, _post_account_txn
from ..models import Garage, GarageLedgerEntry as G, AccountTransaction, Vehicle, MaintenanceRecord


def lock_garage(org, garage_id):
    g = Garage.objects.select_for_update().filter(pk=garage_id, organization=org).first()
    if not g:
        raise FinanceError('not_found', 'Garage not found', status=404)
    return g


def garage_balance(garage):
    return G.objects.filter(garage=garage).aggregate(v=Sum('delta'))['v'] or ZERO


def _sync(garage):
    agg = G.objects.filter(garage=garage).aggregate(pos=Sum('delta', filter=Q(delta__gt=0)), neg=Sum('delta', filter=Q(delta__lt=0)))
    garage.total_bill = agg['pos'] or ZERO
    garage.total_paid = -(agg['neg'] or ZERO)
    garage.save(update_fields=['total_bill', 'total_paid'])


def post(org, garage, entry_type, delta, on_date, user=None, unique_key=None, **extra):
    delta = Decimal(delta)
    if delta == 0:
        return None, False
    if unique_key:
        prior = G.objects.filter(organization=org, unique_key=unique_key).first()
        if prior:
            return prior, False
    try:
        with transaction.atomic():
            e = G.objects.create(organization=org, garage=garage, entry_type=entry_type, delta=delta, date=on_date, unique_key=unique_key,
                                 created_by=user if getattr(user, 'pk', None) else None, **extra)
    except IntegrityError:
        prior = G.objects.filter(organization=org, unique_key=unique_key).first()
        if prior:
            return prior, False
        raise
    _sync(garage)
    return e, True


# ── bills (called from the maintenance endpoints) ──────────────────────────

def bill_for_maintenance(org, garage, maintenance, user=None):
    vehicle = maintenance.vehicle
    n = G.objects.filter(garage=garage, maintenance_id=maintenance.pk, entry_type=G.EntryType.BILL).count() + 1
    return post(org, garage, G.EntryType.BILL, maintenance.total_cost, maintenance.date, user, unique_key=f'mnt:{maintenance.pk}:bill:g{garage.pk}:{n}',
                vehicle=vehicle, maintenance_id=maintenance.pk, reference=maintenance.maintenance_id,
                description=f'{maintenance.maintenance_id} – {maintenance.vehicle_reg}: {maintenance.service_type}')


def adjust_for_maintenance(org, garage, maintenance, old_total, user=None):
    diff = maintenance.total_cost - old_total
    n = G.objects.filter(garage=garage, maintenance_id=maintenance.pk, entry_type=G.EntryType.ADJUSTMENT).count() + 1
    return post(org, garage, G.EntryType.ADJUSTMENT, diff, date.today(), user, unique_key=f'mnt:{maintenance.pk}:adj:g{garage.pk}:{n}',
                vehicle=maintenance.vehicle, maintenance_id=maintenance.pk, reference=maintenance.maintenance_id,
                description=f'Cost of {maintenance.maintenance_id} changed from {old_total} to {maintenance.total_cost}')


def cancel_maintenance_bill(org, garage, maintenance, user=None):
    """A deleted maintenance job removes what it billed (net of earlier adjustments) with an adjustment; history stays."""
    net = G.objects.filter(garage=garage, maintenance_id=maintenance.pk, entry_type__in=[G.EntryType.BILL, G.EntryType.ADJUSTMENT]).aggregate(v=Sum('delta'))['v'] or ZERO
    if net == 0:
        net = maintenance.total_cost   # job predates the ledger (covered by the legacy balance)
    n = G.objects.filter(garage=garage, maintenance_id=maintenance.pk, description__endswith='removed from this garage').count() + 1
    return post(org, garage, G.EntryType.ADJUSTMENT, -net, date.today(), user, unique_key=f'mnt:{maintenance.pk}:cancel:g{garage.pk}:{n}',
                vehicle=maintenance.vehicle, maintenance_id=maintenance.pk, reference=maintenance.maintenance_id, description=f'{maintenance.maintenance_id} removed from this garage')


# ── payments ───────────────────────────────────────────────────────────────

def pay_garage(org, garage_id, user, amount, on_date, method='Cash', reference='', note='', vehicle=None, key=None):
    """Pay a garage. Over-payment, duplicate vendor reference and replays are handled; returns (entry, outstanding_after, replayed)."""
    amount = money(amount)
    reference = (reference or '').strip()
    with transaction.atomic():
        garage = lock_garage(org, garage_id)
        if key:
            prior = G.objects.filter(organization=org, unique_key=f'gpay:{key}').first()
            if prior:
                return prior, garage_balance(garage), True
        if reference and G.objects.filter(garage=garage, entry_type=G.EntryType.PAYMENT, reference=reference, reverses__isnull=True, reversed_by__isnull=True).exists():
            raise FinanceError('duplicate_reference', f'Payment reference "{reference}" was already used for this garage', status=409)
        outstanding = garage_balance(garage)
        if amount > outstanding:
            raise FinanceError('exceeds_balance', f'Payment (৳{amount}) exceeds the outstanding payable (৳{outstanding})')
        entry, _ = post(org, garage, G.EntryType.PAYMENT, -amount, on_date, user, unique_key=f'gpay:{key}' if key else None, vehicle=vehicle,
                        payment_method=method, reference=reference, description=note or f'Payment to {garage.name}')
        _post_account_txn(org, user, on_date, 'Expense', 'Garage Bill', garage.name, amount, method, reference, f'Payment to garage {garage.name}: {note}',
                          vehicle.registration if vehicle else '', 'garage_payment', f'gpay-txn:{entry.pk}')
        audit(org, user, 'garage.payment', 'garage', garage.pk, after={'amount': amount, 'reference': reference, 'date': on_date})
        return entry, garage_balance(garage), False


def reverse_garage_payment(org, entry_id, user, reason=''):
    if not (reason or '').strip():
        raise FinanceError('reason_required', 'A reason is required to reverse a payment')
    with transaction.atomic():
        e = G.objects.select_related('garage').filter(pk=entry_id, organization=org).first()
        if not e:
            raise FinanceError('not_found', 'Payment not found', status=404)
        garage = lock_garage(org, e.garage_id)
        if e.entry_type != G.EntryType.PAYMENT:
            raise FinanceError('not_a_payment', 'Only payments can be reversed here', status=409)
        if G.objects.filter(reverses=e).exists():
            raise FinanceError('already_reversed', 'Payment was already reversed', status=409)
        rev, _ = post(org, garage, G.EntryType.REVERSAL, -e.delta, date.today(), user, unique_key=f'gpay-rev:{e.pk}', vehicle=e.vehicle,
                      reference=e.reference, description=f'Reversal of payment #{e.pk}: {reason}'[:500], reverses=e)
        orig_txn = AccountTransaction.objects.filter(organization=org, idempotency_key=f'gpay-txn:{e.pk}').first()
        if orig_txn:
            _post_account_txn(org, user, date.today(), 'Income', 'Reversal: Garage Bill', garage.name, orig_txn.amount, orig_txn.payment_method,
                              orig_txn.reference, f'Reversal of {orig_txn.transaction_id}: {reason}', orig_txn.vehicle_reg, 'garage_reversal', f'gpay-txn-rev:{e.pk}')
        audit(org, user, 'garage.payment.reverse', 'garage', garage.pk, reason=reason)
        return rev


# ── statements ─────────────────────────────────────────────────────────────

def _row(e):
    return {'id': e.pk, 'date': e.date.isoformat(), 'type': e.entry_type, 'delta': S(e.delta), 'vehicle': e.vehicle.registration if e.vehicle_id else '',
            'reference': e.reference, 'method': e.payment_method, 'description': e.description,
            'reversed': G.objects.filter(reverses=e).exists() if e.entry_type == G.EntryType.PAYMENT else False}


def garage_statement(org, garage, date_from=None, date_to=None):
    qs = G.objects.filter(garage=garage).select_related('vehicle')
    rows = qs
    if date_from:
        rows = rows.filter(date__gte=date_from)
    if date_to:
        rows = rows.filter(date__lte=date_to)
    bills = qs.filter(delta__gt=0).aggregate(v=Sum('delta'))['v'] or ZERO
    paid = -(qs.filter(delta__lt=0).aggregate(v=Sum('delta'))['v'] or ZERO)
    by_vehicle = {}
    for e in qs.exclude(entry_type=G.EntryType.LEGACY):
        if e.vehicle_id:
            r = by_vehicle.setdefault(e.vehicle.registration, {'billed': ZERO, 'paid': ZERO})
            if e.entry_type in (G.EntryType.BILL, G.EntryType.ADJUSTMENT):
                r['billed'] += e.delta
            else:
                r['paid'] += -e.delta
    legacy = qs.filter(entry_type=G.EntryType.LEGACY).aggregate(v=Sum('delta'))['v'] or ZERO
    return {'garage': {'id': garage.pk, 'name': garage.name, 'garageId': garage.garage_id}, 'totalBilled': S(bills), 'totalPaid': S(paid),
            'outstanding': S(garage_balance(garage)), 'carriedForward': S(legacy),
            'vehicleBreakdown': [{'vehicle': k, 'billed': S(v['billed']), 'paid': S(v['paid'])} for k, v in by_vehicle.items()],
            'history': [_row(e) for e in rows.order_by('-date', '-id')]}


def reconcile_garages(org=None):
    """Independent audit: cache == ledger, ledger-only payables never negative, vehicle expense = sum of that vehicle's maintenance cost
    for jobs billed through the ledger."""
    problems = []
    qs = Garage.objects.all() if not org else Garage.objects.filter(organization=org)
    for g in qs:
        pos = neg = ZERO
        for d in G.objects.filter(garage=g).values_list('delta', flat=True):
            if d > 0:
                pos += d
            else:
                neg += -d
        if G.objects.filter(garage=g).exists():
            if g.total_bill != pos or g.total_paid != neg:
                problems.append(f'garage {g.pk} {g.name}: cache {g.total_bill}/{g.total_paid} != ledger {pos}/{neg}')
            if pos - neg < 0:
                problems.append(f'garage {g.pk} {g.name}: negative payable {pos - neg}')
        for e in G.objects.filter(garage=g, entry_type=G.EntryType.BILL):
            m = MaintenanceRecord.objects.filter(pk=e.maintenance_id).first()
            if m is not None:
                net = G.objects.filter(garage=g, maintenance_id=m.pk, entry_type__in=['bill', 'adjustment']).aggregate(v=Sum('delta'))['v'] or ZERO
                if net != m.total_cost:
                    problems.append(f'maintenance {m.pk}: billed {net} != cost {m.total_cost}')
    return problems
