"""Client change-request API: driver salary/advance/fuel-gas, opening balances, owner rent accrual, vehicle payments,
daily report, custom transactions, replacement drivers, owner/client agreements, documents.

Unlike the older `tasin` router these endpoints require a logged-in session, a membership of the organization and the
matching RBAC module action. Money is returned as decimal strings.
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List
from django.db import transaction
from django.db.models import Q
from ninja import Router, Schema, Header
from .errors import ApiError
from ..models import (
    Organization, Membership, Party, Vehicle, VehiclePaper, AccountTransaction, SalaryPayment, RentAccrual,
    ReplacementAssignment, TasinAgreement, PartyDetail, PartyLedgerEntry,
)
from ..services import finance as fin
from ..services.audit import audit
from .tasin_auth import authorize_org, tasin_auth

Date = date  # schema fields named `date` shadow the type inside class bodies
router = Router(auth=tasin_auth)
D = Decimal


def _ctx(request, org_id, module, action='view'):
    """Return (org, user) after membership, portal-role and RBAC checks (shared with the Tasin router auth)."""
    org = Organization.objects.filter(pk=org_id).first()
    if not org:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    authorize_org(request.user, org, module, action)
    return org, request.user


def _party(org, pk, kind):
    p = Party.objects.filter(pk=pk, organization=org, kind=kind).first()
    if not p:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    return p


# ─── schemas ────────────────────────────────────────────────────────────────

class OpeningIn(Schema):
    balance_type: str
    amount: Decimal
    date: Optional[Date] = None
    direction: Optional[str] = None
    description: str = ''
    reference: str = ''
    attachment: str = ''


class AdvanceIn(Schema):
    amount: Decimal
    date: Date
    payment_method: str = 'Cash'
    reference: str = ''
    description: str = ''


class FuelGasIn(Schema):
    amount: Decimal
    date: Date
    vehicle_id: Optional[int] = None
    direction: str = 'driver_owes'
    payment_method: str = 'Cash'
    reference: str = ''
    description: str = ''


class SalaryPaymentIn(Schema):
    salary_month: str
    payment_date: Date
    payment_method: str = 'Cash'
    reference: str = ''
    notes: str = ''
    advance_adjustment: Decimal = D(0)
    fuel_gas_settlement: Decimal = D(0)
    fuel_gas_mode: str = 'none'
    fuel_gas_direction: str = 'driver_owes'
    amount_paid: Optional[Decimal] = None


class ReverseIn(Schema):
    reason: str = ''


class RentAccrualIn(Schema):
    period: str
    owner_id: Optional[int] = None
    vehicle_ids: Optional[List[int]] = None


class PartyPaymentIn(Schema):
    amount: Decimal
    date: Date
    payment_method: str = 'Cash'
    reference: str = ''
    note: str = ''


class VehiclePaymentIn(Schema):
    direction: str
    amount: Decimal
    date: Date
    counterparty: str = ''
    party_id: Optional[int] = None
    invoice_id: Optional[int] = None
    payment_method: str = 'Cash'
    reference: str = ''
    description: str = ''


class CustomTxnIn(Schema):
    transaction_type: str
    title: str
    amount: Decimal
    date: Date
    payment_method: str = 'Cash'
    description: str = ''
    related_party: str = ''
    vehicle_reg: str = ''
    reference: str = ''


class ReplacementIn(Schema):
    date: Date
    original_driver_id: int
    replacement_driver_id: int
    vehicle_id: int
    reason: str = ''
    notes: str = ''


class AgreementIn(Schema):
    agreement_type: str
    party_id: int
    vehicle_id: Optional[int] = None
    title: str
    start_date: Date
    end_date: Optional[Date] = None
    monthly_amount: Decimal = D(0)
    default_terms: List[str] = []
    custom_terms: List[str] = []
    attachment: str = ''
    finalize: bool = False


class PaperIn(Schema):
    paper_type: str
    title: str = ''
    paper_number: str = ''
    issue_date: Optional[Date] = None
    expiry_date: Optional[Date] = None
    document_url: str = ''


PAPER_LABELS = dict(VehiclePaper.PAPER_TYPE_CHOICES)
PAPER_LABELS.update({'driving_license': 'Driving License'})


# ─── opening balances (driver / owner / client) ─────────────────────────────

@router.post('/orgs/{org_id}/parties/{party_id}/opening-balance')
def post_opening_balance(request, org_id: int, party_id: int, payload: OpeningIn):
    party = Party.objects.filter(pk=party_id, organization_id=org_id).first()
    if not party:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    module = {'owner': 'owners', 'driver': 'drivers', 'customer': 'clients'}[party.kind]
    org, user = _ctx(request, org_id, module, 'create')
    e = fin.set_opening_balance(org, party, user, payload.balance_type, payload.amount, payload.date, payload.direction,
                                payload.description, payload.reference, payload.attachment)
    return {'success': True, 'entryId': e.pk, 'account': e.account, 'amount': fin.S(e.delta)}


@router.get('/orgs/{org_id}/parties/{party_id}/ledger')
def party_ledger(request, org_id: int, party_id: int, date_from: Optional[date] = None, date_to: Optional[date] = None):
    party = Party.objects.filter(pk=party_id, organization_id=org_id).first()
    if not party:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    module = {'owner': 'owners', 'driver': 'drivers', 'customer': 'clients'}[party.kind]
    org, user = _ctx(request, org_id, module, 'view')
    qs = PartyLedgerEntry.objects.filter(party=party)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return [fin._entry_dict(e) for e in qs.select_related('vehicle').order_by('-date', '-id')]


# ─── driver finance ─────────────────────────────────────────────────────────

@router.get('/orgs/{org_id}/drivers/{driver_id}/finance')
def driver_finance(request, org_id: int, driver_id: int, date_from: Optional[date] = None, date_to: Optional[date] = None):
    org, user = _ctx(request, org_id, 'drivers', 'view')
    return fin.driver_summary(org, _party(org, driver_id, 'driver'), date_from, date_to)


@router.post('/orgs/{org_id}/drivers/{driver_id}/advances')
def driver_advance(request, org_id: int, driver_id: int, payload: AdvanceIn, idem: Optional[str] = Header(None, alias='Idempotency-Key')):
    org, user = _ctx(request, org_id, 'drivers', 'create')
    e = fin.issue_advance(org, _party(org, driver_id, 'driver'), user, payload.amount, payload.date, payload.payment_method, payload.reference, payload.description, idem)
    return {'success': True, 'entryId': e.pk, 'remainingAdvance': fin.S(fin.balance(e.party, fin.A.DRIVER_ADVANCE))}


@router.post('/orgs/{org_id}/drivers/{driver_id}/fuel-gas')
def driver_fuel_gas(request, org_id: int, driver_id: int, payload: FuelGasIn, idem: Optional[str] = Header(None, alias='Idempotency-Key')):
    org, user = _ctx(request, org_id, 'drivers', 'create')
    vehicle = Vehicle.objects.filter(pk=payload.vehicle_id, organization=org).first() if payload.vehicle_id else None
    if payload.vehicle_id and not vehicle:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    e = fin.issue_fuel_gas(org, _party(org, driver_id, 'driver'), user, payload.amount, payload.date, vehicle, payload.reference, payload.description,
                           payload.direction, payload.payment_method, idem)
    return {'success': True, 'entryId': e.pk, 'account': e.account}


@router.post('/orgs/{org_id}/drivers/{driver_id}/salary-payments')
def driver_salary_payment(request, org_id: int, driver_id: int, payload: SalaryPaymentIn, idem: Optional[str] = Header(None, alias='Idempotency-Key')):
    org, user = _ctx(request, org_id, 'drivers', 'create')
    sp, replayed = fin.pay_salary(org, driver_id, user, payload.salary_month, payload.payment_date, payload.payment_method, payload.reference, payload.notes,
                                  payload.advance_adjustment, payload.fuel_gas_settlement, payload.fuel_gas_mode, payload.fuel_gas_direction,
                                  payload.amount_paid, idem)
    return {'success': True, 'replayed': replayed, 'id': sp.pk, 'salaryMonth': sp.salary_month, 'monthlySalary': fin.S(sp.monthly_salary),
            'advanceAdjusted': fin.S(sp.advance_adjusted), 'fuelGasSettled': fin.S(sp.fuel_gas_settled), 'netPayable': fin.S(sp.net_payable),
            'amountPaid': fin.S(sp.amount_paid), 'remainingSalaryDue': fin.S(sp.remaining_salary_due),
            'remainingAdvance': fin.S(fin.balance(sp.driver, fin.A.DRIVER_ADVANCE)), 'remainingFuelGas': fin.S(fin.balance(sp.driver, sp.fuel_gas_account))}


@router.post('/orgs/{org_id}/salary-payments/{payment_id}/reverse')
def reverse_salary(request, org_id: int, payment_id: int, payload: ReverseIn):
    org, user = _ctx(request, org_id, 'drivers', 'approve')
    sp = fin.reverse_salary_payment(org, payment_id, user, payload.reason)
    return {'success': True, 'id': sp.pk, 'status': sp.status}


# ─── owner finance & rent accrual ───────────────────────────────────────────

@router.get('/orgs/{org_id}/owners/{owner_id}/finance')
def owner_finance(request, org_id: int, owner_id: int, date_from: Optional[date] = None, date_to: Optional[date] = None):
    org, user = _ctx(request, org_id, 'owners', 'view')
    return fin.owner_summary(org, _party(org, owner_id, 'owner'), date_from, date_to)


@router.get('/orgs/{org_id}/rent-accruals/preview')
def rent_preview(request, org_id: int, period: str, owner_id: Optional[int] = None):
    org, user = _ctx(request, org_id, 'owners', 'view')
    return {'period': fin.month_key(period), 'lines': fin.eligible_rent_lines(org, period, owner_id)}


@router.post('/orgs/{org_id}/rent-accruals')
def rent_accrue(request, org_id: int, payload: RentAccrualIn):
    org, user = _ctx(request, org_id, 'owners', 'create')
    return fin.accrue_rent(org, user, payload.period, payload.owner_id, payload.vehicle_ids)


@router.post('/orgs/{org_id}/rent-accruals/{accrual_id}/reverse')
def rent_reverse(request, org_id: int, accrual_id: int, payload: ReverseIn):
    org, user = _ctx(request, org_id, 'owners', 'approve')
    a = fin.reverse_rent_accrual(org, user, accrual_id, payload.reason)
    return {'success': True, 'id': a.pk, 'status': a.status}


@router.post('/orgs/{org_id}/owners/{owner_id}/ledger-payments')
def owner_ledger_payment(request, org_id: int, owner_id: int, payload: PartyPaymentIn, idem: Optional[str] = Header(None, alias='Idempotency-Key')):
    org, user = _ctx(request, org_id, 'owners', 'create')
    e, bal, replayed = fin.party_payment(org, owner_id, 'owner', user, payload.amount, payload.date, payload.payment_method, payload.reference, payload.note, idem)
    return {'success': True, 'replayed': replayed, 'remainingPayable': fin.S(bal)}


# ─── client finance ─────────────────────────────────────────────────────────

@router.get('/orgs/{org_id}/clients/{client_id}/finance')
def client_finance(request, org_id: int, client_id: int, date_from: Optional[date] = None, date_to: Optional[date] = None):
    org, user = _ctx(request, org_id, 'clients', 'view')
    return fin.client_summary(org, _party(org, client_id, 'customer'), date_from, date_to)


@router.post('/orgs/{org_id}/clients/{client_id}/ledger-payments')
def client_ledger_payment(request, org_id: int, client_id: int, payload: PartyPaymentIn, idem: Optional[str] = Header(None, alias='Idempotency-Key')):
    org, user = _ctx(request, org_id, 'clients', 'create')
    e, bal, replayed = fin.party_payment(org, client_id, 'customer', user, payload.amount, payload.date, payload.payment_method, payload.reference, payload.note, idem)
    return {'success': True, 'replayed': replayed, 'remainingReceivable': fin.S(bal)}


# ─── vehicle payments ───────────────────────────────────────────────────────

@router.post('/orgs/{org_id}/vehicles/{vehicle_id}/payments')
def vehicle_payment(request, org_id: int, vehicle_id: int, payload: VehiclePaymentIn, idem: Optional[str] = Header(None, alias='Idempotency-Key')):
    org, user = _ctx(request, org_id, 'accounts', 'create')
    vehicle = Vehicle.objects.filter(pk=vehicle_id, organization=org).first()
    if not vehicle:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    txn, replayed = fin.vehicle_payment(org, user, payload.direction, vehicle, payload.amount, payload.date, payload.counterparty, payload.payment_method,
                                        payload.reference, payload.description, payload.invoice_id, payload.party_id, idem)
    return {'success': True, 'replayed': replayed, 'id': txn.pk, 'transactionId': txn.transaction_id, 'type': txn.transaction_type, 'amount': fin.S(txn.amount)}


@router.get('/orgs/{org_id}/vehicles/{vehicle_id}/payments')
def vehicle_payments(request, org_id: int, vehicle_id: int):
    org, user = _ctx(request, org_id, 'accounts', 'view')
    vehicle = Vehicle.objects.filter(pk=vehicle_id, organization=org).first()
    if not vehicle:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    qs = AccountTransaction.objects.filter(organization=org, vehicle_reg=vehicle.registration, source='vehicle_payment').order_by('-date', '-id')
    return [{'id': t.pk, 'transactionId': t.transaction_id, 'date': t.date.isoformat(), 'type': t.transaction_type, 'counterparty': t.related_person,
             'amount': fin.S(t.amount), 'method': t.payment_method, 'reference': t.reference, 'description': t.description} for t in qs]


# ─── daily report & custom transactions ─────────────────────────────────────

@router.get('/orgs/{org_id}/daily-report')
def daily_report(request, org_id: int, day: date):
    org, user = _ctx(request, org_id, 'expenses', 'view')
    out = fin.daily_report(org, day)
    from ..services.tasin import get_or_create_company_profile
    out['businessName'] = get_or_create_company_profile(org).company_name
    from django.utils import timezone
    out['generatedAt'] = timezone.localtime().isoformat(timespec='seconds')
    return out


@router.post('/orgs/{org_id}/transactions/custom')
def custom_txn(request, org_id: int, payload: CustomTxnIn, idem: Optional[str] = Header(None, alias='Idempotency-Key')):
    org, user = _ctx(request, org_id, 'accounts', 'create')
    t, replayed = fin.custom_transaction(org, user, payload.transaction_type, payload.title, payload.amount, payload.date, payload.payment_method,
                                         payload.description, payload.related_party, payload.vehicle_reg, payload.reference, idem)
    return {'success': True, 'replayed': replayed, 'id': t.pk, 'transactionId': t.transaction_id, 'category': t.category, 'title': t.custom_title}


@router.get('/orgs/{org_id}/transactions/search')
def search_transactions(request, org_id: int, q: str = '', date_from: Optional[date] = None, date_to: Optional[date] = None,
                        category: str = '', txn_type: str = ''):
    org, user = _ctx(request, org_id, 'accounts', 'view')
    qs = AccountTransaction.objects.filter(organization=org)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if category:
        qs = qs.filter(Q(category=category) | Q(custom_title__iexact=category))
    if txn_type:
        qs = qs.filter(transaction_type=txn_type)
    if q:
        qs = qs.filter(Q(description__icontains=q) | Q(custom_title__icontains=q) | Q(reference__icontains=q) | Q(related_person__icontains=q) | Q(category__icontains=q))
    return [{'id': t.pk, 'transactionId': t.transaction_id, 'date': t.date.isoformat(), 'type': t.transaction_type,
             'category': t.custom_title if t.category == 'Custom' and t.custom_title else t.category, 'isCustom': t.category == 'Custom',
             'party': t.related_person, 'vehicleReg': t.vehicle_reg, 'amount': fin.S(t.amount), 'paymentMethod': t.payment_method,
             'reference': t.reference, 'description': t.description} for t in qs[:500]]


# ─── replacement drivers ────────────────────────────────────────────────────

def _repl(r):
    return {'id': r.pk, 'date': r.date.isoformat(), 'originalDriverId': r.original_driver_id, 'originalDriver': r.original_driver.name,
            'replacementDriverId': r.replacement_driver_id, 'replacementDriver': r.replacement_driver.name, 'vehicleId': r.vehicle_id,
            'vehicle': r.vehicle.registration, 'reason': r.reason, 'notes': r.notes, 'createdAt': r.created_at.isoformat()}


@router.get('/orgs/{org_id}/replacements')
def list_replacements(request, org_id: int, date_from: Optional[date] = None, date_to: Optional[date] = None, driver_id: Optional[int] = None,
                      replacement_driver_id: Optional[int] = None, vehicle_id: Optional[int] = None):
    org, user = _ctx(request, org_id, 'drivers', 'view')
    qs = ReplacementAssignment.objects.filter(organization=org).select_related('original_driver', 'replacement_driver', 'vehicle')
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if driver_id:
        qs = qs.filter(original_driver_id=driver_id)
    if replacement_driver_id:
        qs = qs.filter(replacement_driver_id=replacement_driver_id)
    if vehicle_id:
        qs = qs.filter(vehicle_id=vehicle_id)
    return [_repl(r) for r in qs]


@router.post('/orgs/{org_id}/replacements')
def create_replacement(request, org_id: int, payload: ReplacementIn):
    org, user = _ctx(request, org_id, 'drivers', 'create')
    r = fin.record_replacement(org, user, payload.date, payload.original_driver_id, payload.replacement_driver_id, payload.vehicle_id, payload.reason, payload.notes)
    return {'success': True, **_repl(r)}


@router.delete('/orgs/{org_id}/replacements/{rid}')
def delete_replacement(request, org_id: int, rid: int):
    org, user = _ctx(request, org_id, 'drivers', 'delete')
    r = ReplacementAssignment.objects.filter(pk=rid, organization=org).first()
    if not r:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    audit(org, user, 'attendance.replacement.delete', 'replacement', r.pk, before=_repl(r))
    r.delete()
    return {'success': True}


# ─── agreements (client / owner) ────────────────────────────────────────────

def _agr(a):
    return {'id': a.pk, 'type': a.agreement_type, 'typeLabel': a.get_agreement_type_display(), 'partyId': a.party_id, 'party': a.party.name,
            'vehicleId': a.vehicle_id, 'vehicle': a.vehicle.registration if a.vehicle_id else '', 'title': a.title, 'startDate': a.start_date.isoformat(),
            'endDate': a.end_date.isoformat() if a.end_date else '', 'monthlyAmount': fin.S(a.monthly_amount), 'defaultTerms': a.default_terms,
            'customTerms': a.custom_terms, 'finalContent': a.final_content, 'attachment': a.attachment, 'status': a.status}


def _validate_agreement(org, p: AgreementIn):
    if p.agreement_type not in ('client', 'owner'):
        raise fin.FinanceError('invalid_agreement_type', 'Agreement type must be client or owner')
    kind = 'customer' if p.agreement_type == 'client' else 'owner'
    party = Party.objects.filter(pk=p.party_id, organization=org, kind=kind).first()
    if not party:
        raise fin.FinanceError('wrong_party', f'A {p.agreement_type} agreement must be linked to a {"client" if kind == "customer" else "owner"}')
    vehicle = None
    if p.vehicle_id:
        vehicle = Vehicle.objects.filter(pk=p.vehicle_id, organization=org).first()
        if not vehicle:
            raise fin.FinanceError('not_found', 'Record not found', status=404)
        if p.agreement_type == 'owner' and vehicle.owner_id != party.pk:
            raise fin.FinanceError('vehicle_owner_mismatch', 'This vehicle does not belong to the selected owner')
    if p.end_date and p.end_date < p.start_date:
        raise fin.FinanceError('invalid_dates', 'End date cannot be before the start date')
    if not p.title.strip():
        raise fin.FinanceError('title_required', 'Agreement title is required')
    terms = [t for t in (x.rstrip() for x in p.custom_terms) if t.strip()]
    if any(len(t) > 20000 for t in terms):
        raise fin.FinanceError('term_too_long', 'A custom term is too long')
    return party, vehicle, terms


def _final_content(a):
    lines = [a.title, f'{a.get_agreement_type_display()} – {a.party.name}', f'Period: {a.start_date} to {a.end_date or "open-ended"}', '']
    if a.monthly_amount:
        lines.append(f'Monthly amount: ৳{a.monthly_amount}')
        lines.append('')
    lines.append('Terms & Conditions')
    n = 1
    for t in list(a.default_terms) + list(a.custom_terms):
        lines.append(f'{n}. {t}')
        n += 1
    return '\n'.join(lines)


@router.get('/orgs/{org_id}/tasin-agreements')
def list_tasin_agreements(request, org_id: int, type: str = '', q: str = ''):
    org, user = _ctx(request, org_id, 'agreements', 'view')
    qs = TasinAgreement.objects.filter(organization=org).select_related('party', 'vehicle')
    if type in ('client', 'owner'):
        qs = qs.filter(agreement_type=type)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(party__name__icontains=q) | Q(vehicle__registration__icontains=q))
    return [_agr(a) for a in qs]


@router.post('/orgs/{org_id}/tasin-agreements')
def create_tasin_agreement(request, org_id: int, payload: AgreementIn):
    org, user = _ctx(request, org_id, 'agreements', 'create')
    party, vehicle, terms = _validate_agreement(org, payload)
    with transaction.atomic():
        a = TasinAgreement.objects.create(organization=org, agreement_type=payload.agreement_type, party=party, vehicle=vehicle, title=payload.title.strip(),
                                          start_date=payload.start_date, end_date=payload.end_date, monthly_amount=payload.monthly_amount,
                                          default_terms=payload.default_terms, custom_terms=terms, attachment=payload.attachment, created_by=user)
        if payload.finalize:
            a.final_content = _final_content(a)
            a.status = 'final'
            a.save()
        audit(org, user, 'agreement.create', 'tasin_agreement', a.pk, after={'type': a.agreement_type, 'party': party.name})
    return {'success': True, **_agr(a)}


@router.put('/orgs/{org_id}/tasin-agreements/{agreement_id}')
def update_tasin_agreement(request, org_id: int, agreement_id: int, payload: AgreementIn):
    org, user = _ctx(request, org_id, 'agreements', 'edit')
    a = TasinAgreement.objects.filter(pk=agreement_id, organization=org).first()
    if not a:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    if a.status == 'final':
        raise fin.FinanceError('agreement_final', 'A finalized agreement can no longer be edited', status=409)
    if payload.agreement_type != a.agreement_type:
        raise fin.FinanceError('type_locked', 'The agreement type cannot be changed', status=409)
    party, vehicle, terms = _validate_agreement(org, payload)
    a.party, a.vehicle, a.title = party, vehicle, payload.title.strip()
    a.start_date, a.end_date, a.monthly_amount = payload.start_date, payload.end_date, payload.monthly_amount
    a.default_terms, a.custom_terms, a.attachment = payload.default_terms, terms, payload.attachment
    if payload.finalize:
        a.final_content = _final_content(a)
        a.status = 'final'
    a.save()
    audit(org, user, 'agreement.update', 'tasin_agreement', a.pk)
    return {'success': True, **_agr(a)}


# ─── documents (vehicle papers, one source for Vehicle Profile and Global Documents) ───

MAX_DOC_URL = 255


def _paper(p):
    label = PAPER_LABELS.get(p.paper_type, p.paper_type)
    return {'id': p.pk, 'vehicleId': p.vehicle_id, 'vehicle': p.vehicle.registration, 'paperType': p.paper_type, 'typeLabel': label,
            'title': p.title or f'{label} – {p.vehicle.registration}', 'paperNumber': p.paper_number,
            'issueDate': p.issue_date.isoformat() if p.issue_date else '', 'expiryDate': p.expiry_date.isoformat() if p.expiry_date else '',
            'status': _paper_status(p), 'documentUrl': p.document_url, 'updatedAt': p.updated_at.isoformat()}


def _paper_status(p):
    if not p.expiry_date:
        return 'Valid'
    days = (p.expiry_date - date.today()).days
    return 'Expired' if days < 0 else 'Expiring Soon' if days <= 30 else 'Valid'


def _validate_paper(p: PaperIn):
    if p.paper_type not in dict(VehiclePaper.PAPER_TYPE_CHOICES):
        raise fin.FinanceError('invalid_paper_type', 'Unknown document type')
    if p.issue_date and p.expiry_date and p.expiry_date < p.issue_date:
        raise fin.FinanceError('invalid_dates', 'Expiry date cannot be before the issue date')
    if len(p.document_url) > MAX_DOC_URL or len(p.title) > 200:
        raise fin.FinanceError('too_long', 'Title or document link is too long')


@router.get('/orgs/{org_id}/documents-all')
def all_documents(request, org_id: int, vehicle_id: Optional[int] = None, paper_type: str = '', q: str = ''):
    org, user = _ctx(request, org_id, 'agreements', 'view')
    qs = VehiclePaper.objects.filter(organization=org).select_related('vehicle').order_by('-id')
    if vehicle_id:
        qs = qs.filter(vehicle_id=vehicle_id)
    if paper_type:
        qs = qs.filter(paper_type=paper_type)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(paper_number__icontains=q) | Q(vehicle__registration__icontains=q))
    return [_paper(p) for p in qs]


@router.post('/orgs/{org_id}/vehicles/{vehicle_id}/documents')
def add_document(request, org_id: int, vehicle_id: int, payload: PaperIn):
    org, user = _ctx(request, org_id, 'agreements', 'create')
    v = Vehicle.objects.filter(pk=vehicle_id, organization=org).first()
    if not v:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    _validate_paper(payload)
    if payload.paper_number and VehiclePaper.objects.filter(organization=org, vehicle=v, paper_type=payload.paper_type, paper_number=payload.paper_number).exists():
        raise fin.FinanceError('duplicate_document', 'This document number is already recorded for the vehicle and type', status=409)
    p = VehiclePaper.objects.create(organization=org, vehicle=v, paper_type=payload.paper_type, title=payload.title.strip(), paper_number=payload.paper_number,
                                    issue_date=payload.issue_date, expiry_date=payload.expiry_date, document_url=payload.document_url)
    p.status = _paper_status(p)
    p.save(update_fields=['status'])
    audit(org, user, 'document.create', 'vehicle_paper', p.pk, after={'type': p.paper_type, 'vehicle': v.registration})
    return {'success': True, **_paper(p)}


@router.put('/orgs/{org_id}/documents-all/{paper_id}')
def update_document(request, org_id: int, paper_id: int, payload: PaperIn):
    org, user = _ctx(request, org_id, 'agreements', 'edit')
    p = VehiclePaper.objects.filter(pk=paper_id, organization=org).select_related('vehicle').first()
    if not p:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    _validate_paper(payload)
    p.paper_type, p.title, p.paper_number = payload.paper_type, payload.title.strip(), payload.paper_number
    p.issue_date, p.expiry_date, p.document_url = payload.issue_date, payload.expiry_date, payload.document_url
    p.status = _paper_status(p)
    p.save()
    audit(org, user, 'document.update', 'vehicle_paper', p.pk)
    return {'success': True, **_paper(p)}


@router.delete('/orgs/{org_id}/documents-all/{paper_id}')
def delete_document(request, org_id: int, paper_id: int):
    org, user = _ctx(request, org_id, 'agreements', 'delete')
    p = VehiclePaper.objects.filter(pk=paper_id, organization=org).first()
    if not p:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    audit(org, user, 'document.delete', 'vehicle_paper', p.pk, before={'type': p.paper_type, 'number': p.paper_number})
    p.delete()
    return {'success': True}


@router.get('/orgs/{org_id}/vehicles/{vehicle_id}/billing')
def vehicle_billing(request, org_id: int, vehicle_id: int, month: Optional[str] = None):
    org, user = _ctx(request, org_id, 'accounts', 'view')
    vehicle = Vehicle.objects.filter(pk=vehicle_id, organization=org).select_related('owner').first()
    if not vehicle:
        raise fin.FinanceError('not_found', 'Record not found', status=404)
    return fin.vehicle_billing(org, vehicle, month)
