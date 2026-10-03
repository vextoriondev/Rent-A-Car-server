"""Month-end settlement: preview (readiness checks) -> request approval -> approve -> close, and reopen.

Period.closed_at stays the single gate every posting service already checks; the Settlement row is the workflow and the
frozen statement lines around it. Locks are always taken in the order Period -> Settlement."""
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from ..models import Allocation, Charge, Expense, InvoiceLine, Invoice, Obligation, OrgSettings, OwnerOffset, Period, Settlement, SettlementLine
from .helpers import ZERO, DomainError, fingerprint, notice, period_parties
from .statements import statement

BLOCKING='blocking';WARNING='warning'
MSG_PENDING='Pending expense approval blocks close'
MSG_DRAFT='Draft expenses must be submitted or discarded before close'
MSG_NEGATIVE='Negative balance blocks close'

def get_settings(org):
    return OrgSettings.objects.get_or_create(organization=org)[0]

def update_settings(org,user,*,cutoff_day=None,approval_role=None,negative_owner_blocks_close=None):
    s=get_settings(org)
    if cutoff_day is not None:
        if not 1<=cutoff_day<=31:raise DomainError('Cutoff day must be between 1 and 31')
        s.cutoff_day=cutoff_day
    if approval_role is not None:
        if approval_role not in('admin','accountant'):raise DomainError('Approval role must be admin or accountant')
        s.approval_role=approval_role
    if negative_owner_blocks_close is not None:s.negative_owner_blocks_close=negative_owner_blocks_close
    s.save();return s

def exceptions_for(period):
    """Structured readiness exceptions. 'blocking' ones stop approval/close; 'warning' ones are shown but never stop it."""
    out=[]
    cfg=get_settings(period.organization)
    for e in period.expenses.filter(state=Expense.State.PENDING):out.append({'code':'pending_expense','severity':BLOCKING,'message':MSG_PENDING,'ref':e.public_id})
    for e in period.expenses.filter(state=Expense.State.DRAFT):out.append({'code':'draft_expense','severity':BLOCKING,'message':MSG_DRAFT,'ref':e.public_id})
    s=statement(period)
    if s['customer']['due']<0 or s['driver']['due']<0:out.append({'code':'negative_balance','severity':BLOCKING,'message':MSG_NEGATIVE,'ref':''})
    if s['owner']['due']<0:out.append({'code':'owner_owes_operator','severity':BLOCKING if cfg.negative_owner_blocks_close else WARNING,'message':'Owner owes the operator for this month' if not cfg.negative_owner_blocks_close else MSG_NEGATIVE,'ref':''})
    if not period.agreement_version_id:out.append({'code':'missing_agreement_rule','severity':WARNING,'message':'This month was issued before versioned agreement terms; rules come from the agreement itself','ref':''})
    taken=InvoiceLine.objects.filter(invoice__state=Invoice.State.ISSUED).values('obligation_id')
    for o in period.obligations.filter(party__kind='customer').exclude(kind='carry_out').exclude(pk__in=taken):
        out.append({'code':'uninvoiced_obligation','severity':WARNING,'message':f'{o.get_kind_display()} is not on an invoice','ref':str(o.pk)})
    prev=(period.month.replace(day=1)-timedelta(days=1)).replace(day=1)
    prior=Period.objects.filter(organization=period.organization,agreement=period.agreement,month=prev).first()
    if prior:
        if not prior.closed_at:out.append({'code':'prior_period_open','severity':WARNING,'message':f'{prev:%Y-%m} is not closed yet','ref':str(prior.pk)})
        else:
            ps=statement(prior)
            if any(ps[side]['due']!=0 for side in('customer','owner','driver')):out.append({'code':'prior_period_balance','severity':WARNING,'message':f'{prev:%Y-%m} closed with a balance that was not carried forward','ref':str(prior.pk)})
    return out

def preview_settlement(org,period_id):
    period=Period.objects.select_related('agreement','organization').get(pk=period_id,organization=org)
    ex=exceptions_for(period)
    return {'period_id':period.pk,'statement':statement(period),'exceptions':ex,'ready':not any(e['severity']==BLOCKING for e in ex)}

def build_lines(period):
    """Statement rows for the frozen settlement. Each section's rows sum to that party's due."""
    rows=[]
    def add(section,label,amount,st='',sid=''):rows.append((section,label,amount,st,str(sid)))
    for o in period.obligations.select_related('party').order_by('pk'):
        add(o.party.kind,'Carried forward' if o.kind=='carry_out' else o.get_kind_display(),o.amount,'obligation',o.pk)
    for a in Allocation.objects.filter(obligation__period=period).select_related('payment','obligation__party').order_by('pk'):
        add(a.obligation.party.kind,f'Payment {a.payment.reference}'+(' (advance)' if a.payment.kind=='salary_advance' else ''),-a.amount,'payment',a.payment_id)
    for off in OwnerOffset.objects.filter(obligation__period=period,expense__state=Expense.State.POSTED).select_related('expense'):
        add('owner',f'Offset: {off.expense.category} {off.expense.reference}',-off.amount,'expense',off.expense_id)
    s=statement(period)
    for side in('customer','owner','driver'):add(side,'Due',s[side]['due'],'statement',period.pk)
    add('operator','Accrual margin',s['operator']['accrual_margin'],'statement',period.pk)
    add('operator','Recorded cash movement',s['operator']['recorded_cash_movement'],'statement',period.pk)
    add('operator','Operator-borne costs',s['operator']['operator_borne_costs'],'statement',period.pk)
    return rows

def _freeze(settlement,period):
    settlement.lines.all().delete()
    SettlementLine.objects.bulk_create([SettlementLine(settlement=settlement,section=sec,label=lab,amount=amt,source_type=st,source_id=sid,order=i) for i,(sec,lab,amt,st,sid) in enumerate(build_lines(period))])

def _hash(period):
    return fingerprint(statement(period))

def _lock(org,settlement_id):
    s=Settlement.objects.get(pk=settlement_id,organization=org)
    period=Period.objects.select_for_update().select_related('agreement','organization').get(pk=s.period_id)
    return Settlement.objects.select_for_update().get(pk=s.pk),period

def _blocking(period):
    blocks=[e for e in exceptions_for(period) if e['severity']==BLOCKING]
    if blocks:raise DomainError('; '.join(dict.fromkeys(e['message'] for e in blocks)))

@transaction.atomic
def prepare_settlement(org,user,period_id):
    """Create (or return) the draft settlement for a period. Idempotent."""
    period=Period.objects.select_for_update().select_related('agreement').get(pk=period_id,organization=org)
    s,created=Settlement.objects.get_or_create(period=period,defaults={'organization':org,'agreement':period.agreement,'month':period.month,'state':Settlement.State.CLOSED if period.closed_at else Settlement.State.DRAFT,'prepared_by':user,'prepared_at':timezone.now()})
    return s

@transaction.atomic
def request_settlement_approval(org,user,settlement_id):
    s,period=_lock(org,settlement_id)
    if s.state==Settlement.State.CLOSED:raise DomainError('Settlement is already closed')
    _blocking(period)
    _freeze(s,period)
    s.state=Settlement.State.PENDING;s.statement_hash=_hash(period);s.prepared_by=user;s.prepared_at=timezone.now()
    s.approved_by=None;s.approved_at=None
    s.save();return s

@transaction.atomic
def approve_settlement(org,user,settlement_id,role,*,shortcut=False):
    s,period=_lock(org,settlement_id)
    if s.state==Settlement.State.APPROVED:return s
    if s.state!=Settlement.State.PENDING:raise DomainError('Request approval first')
    if not shortcut and role not in('admin',get_settings(org).approval_role):raise DomainError('Your role cannot approve settlements for this organization')
    s.state=Settlement.State.APPROVED;s.approved_by=user;s.approved_at=timezone.now();s.save();return s

@transaction.atomic
def close_settlement(org,user,settlement_id):
    """Idempotent: closing again returns the same closed settlement and posts nothing new (notice event keys are unique)."""
    s,period=_lock(org,settlement_id)
    if s.state==Settlement.State.CLOSED:return s
    if s.state!=Settlement.State.APPROVED:raise DomainError('Settlement must be approved before it can be closed')
    _blocking(period)
    if _hash(period)!=s.statement_hash:raise DomainError('The statement changed since approval; request approval again')
    now=timezone.now()
    period.closed_at=now;period.closed_by=user;period.save(update_fields=['closed_at','closed_by'])
    _freeze(s,period)
    s.state=Settlement.State.CLOSED;s.closed_by=user;s.closed_at=now;s.save()
    suffix='' if s.version==1 else f':v{s.version}'
    parties=period_parties(period)
    for side in('owner','customer','driver'):
        notice(org,f'settlement:{period.pk}:{side}{suffix}',parties[side],'settlement_closed',{'period_id':period.pk,'settlement_id':s.pk,'version':s.version})
    return s

@transaction.atomic
def reopen_settlement(org,user,settlement_id,reason):
    """Reopen the latest closed month of an agreement. Refused once its balance was carried forward (reverse that first)."""
    if not reason.strip():raise DomainError('A reason is required to reopen a settlement')
    s,period=_lock(org,settlement_id)
    if s.state!=Settlement.State.CLOSED:raise DomainError('Only closed settlements can be reopened')
    if Period.objects.filter(agreement=period.agreement,month__gt=period.month,closed_at__isnull=False).exists():raise DomainError('Only the latest closed month can be reopened; reopen the later months first')
    if Charge.objects.filter(period=period,kind='carry_out').exists():raise DomainError('This month balance was already carried forward and cannot be reopened')
    period.closed_at=None;period.closed_by=None;period.save(update_fields=['closed_at','closed_by'])
    s.state=Settlement.State.REOPENED;s.version+=1;s.reopened_by=user;s.reopened_at=timezone.now();s.reopen_reason=reason.strip()[:240]
    s.approved_by=None;s.approved_at=None;s.statement_hash='';s.save();return s

def close_period(org,user,period_id):
    """The frontend's single-button close: prepare -> request -> approve (shortcut, no role check beyond the caller's close permission) -> close.
    Idempotent. Legacy error messages are preserved."""
    period=Period.objects.get(pk=period_id,organization=org)
    if period.closed_at:return period
    with transaction.atomic():
        s=prepare_settlement(org,user,period_id)
        request_settlement_approval(org,user,s.pk)
        approve_settlement(org,user,s.pk,'admin',shortcut=True)
        close_settlement(org,user,s.pk)
    period.refresh_from_db();return period
