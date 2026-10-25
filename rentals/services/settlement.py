"""Month-end settlement: preview (readiness checks) -> request approval -> approve -> close, and reopen.

Period.closed_at stays the single gate every posting service already checks; the Settlement row is the workflow and the
frozen statement lines around it. Locks are always taken in the order Period -> Settlement."""
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from ..models import Allocation, Charge, Expense, InvoiceLine, Invoice, Obligation, OrgSettings, OwnerOffset, Period, Settlement, SettlementLine
from .audit import audit
from .helpers import ZERO, DomainError, fingerprint, notice, period_parties
from .statements import statement

BLOCKING='blocking';WARNING='warning'
import json
from ..i18n import format_month, translate, word

def line_label(code,**params):
    """A settlement line is frozen with a code and its values, so it can be shown in any language later."""
    return json.dumps({'c':code,'p':{k:str(v) for k,v in params.items()}},ensure_ascii=False)

def show_label(stored):
    """(code, text in the current language) for a stored line label; older rows hold plain English text."""
    if stored.startswith('{"c"'):
        d=json.loads(stored);return d['c'],translate(d['c'],d['p'])
    return '',stored

def get_settings(org):
    return OrgSettings.objects.get_or_create(organization=org)[0]

def update_settings(org,user,*,cutoff_day=None,approval_role=None,negative_owner_blocks_close=None,admin_copy_email=None,admin_copy_phone=None):
    s=get_settings(org)
    before={'cutoff_day':s.cutoff_day,'approval_role':s.approval_role,'negative_owner_blocks_close':s.negative_owner_blocks_close,'admin_copy_email':s.admin_copy_email,'admin_copy_phone':s.admin_copy_phone}
    if cutoff_day is not None:
        if not 1<=cutoff_day<=31:raise DomainError('cutoff_day_must_be_between_1_and')
        s.cutoff_day=cutoff_day
    if approval_role is not None:
        if approval_role not in('admin','accountant'):raise DomainError('approval_role_must_be_admin_or_accountant')
        s.approval_role=approval_role
    if negative_owner_blocks_close is not None:s.negative_owner_blocks_close=negative_owner_blocks_close
    if admin_copy_email is not None:
        try:validate_email(admin_copy_email.strip()) if admin_copy_email.strip() else None
        except ValidationError:raise DomainError('admin_copy_email_is_not_a_valid_email')
        s.admin_copy_email=admin_copy_email.strip()
    if admin_copy_phone is not None:s.admin_copy_phone=admin_copy_phone.strip()[:32]
    s.save()
    audit(org,user,'settings.update','org_settings',s.pk,before=before,after={'cutoff_day':s.cutoff_day,'approval_role':s.approval_role,'negative_owner_blocks_close':s.negative_owner_blocks_close,'admin_copy_email':s.admin_copy_email,'admin_copy_phone':s.admin_copy_phone})
    return s

def exceptions_for(period):
    """Structured readiness exceptions. 'blocking' ones stop approval/close; 'warning' ones are shown but never stop it."""
    out=[]
    cfg=get_settings(period.organization)
    for e in period.expenses.filter(state=Expense.State.PENDING):out.append({'code':'pending_expense','severity':BLOCKING,'message':translate('exception_pending_expense',{'ref':f'EXP-{e.pk}'}),'ref':f'EXP-{e.pk}'})
    for e in period.expenses.filter(state=Expense.State.DRAFT):out.append({'code':'draft_expense','severity':BLOCKING,'message':translate('exception_draft_expense',{'ref':f'EXP-{e.pk}'}),'ref':f'EXP-{e.pk}'})
    s=statement(period)
    if s['customer']['due']<0 or s['driver']['due']<0:out.append({'code':'negative_balance','severity':BLOCKING,'message':translate('exception_negative_balance'),'ref':''})
    if s['owner']['due']<0:out.append({'code':'owner_owes_operator','severity':BLOCKING if cfg.negative_owner_blocks_close else WARNING,'message':translate('exception_owner_owes_operator' if not cfg.negative_owner_blocks_close else 'exception_negative_balance'),'ref':''})
    if not period.agreement_version_id:out.append({'code':'missing_agreement_rule','severity':WARNING,'message':translate('exception_missing_agreement_rule'),'ref':''})
    taken=InvoiceLine.objects.filter(invoice__state=Invoice.State.ISSUED).values('obligation_id')
    for o in period.obligations.filter(party__kind='customer').exclude(kind='carry_out').exclude(pk__in=taken):
        out.append({'code':'uninvoiced_obligation','severity':WARNING,'message':translate('exception_uninvoiced_obligation',{'item':translate('kind_'+o.kind)}),'ref':str(o.pk)})
    prev=(period.month.replace(day=1)-timedelta(days=1)).replace(day=1)
    prior=Period.objects.filter(organization=period.organization,agreement=period.agreement,month=prev).first()
    if prior:
        if not prior.closed_at:out.append({'code':'prior_period_open','severity':WARNING,'message':translate('exception_prior_period_open',{'month':format_month(prev)}),'ref':str(prior.pk)})
        else:
            ps=statement(prior)
            if any(ps[side]['due']!=0 for side in('customer','owner','driver')):out.append({'code':'prior_period_balance','severity':WARNING,'message':translate('exception_prior_period_balance',{'month':format_month(prev)}),'ref':str(prior.pk)})
    return out

def _vehicle_only(period):
    if not period.agreement_id:raise DomainError('month_end_closing_applies_to_a_vehicle')

def preview_settlement(org,period_id):
    period=Period.objects.select_related('agreement','organization').get(pk=period_id,organization=org)
    _vehicle_only(period)
    ex=exceptions_for(period)
    return {'period_id':period.pk,'statement':statement(period),'exceptions':ex,'ready':not any(e['severity']==BLOCKING for e in ex)}

def build_lines(period):
    """Statement rows for the frozen settlement. Each section's rows sum to that party's due."""
    rows=[]
    def add(section,label,amount,st='',sid=''):rows.append((section,label,amount,st,str(sid)))
    for o in period.obligations.select_related('party').order_by('pk'):
        add(o.party.kind,line_label('line_carried_forward') if o.kind=='carry_out' else line_label('kind_'+o.kind),o.amount,'obligation',o.pk)
    for a in Allocation.objects.filter(obligation__period=period).select_related('payment','obligation__party').order_by('pk'):
        add(a.obligation.party.kind,line_label('line_payment_advance' if a.payment.kind=='salary_advance' else 'line_payment',reference=a.payment.reference),-a.amount,'payment',a.payment_id)
    for off in OwnerOffset.objects.filter(obligation__period=period,expense__state=Expense.State.POSTED).select_related('expense'):
        add('owner',line_label('line_offset',category=word('category',off.expense.category),reference=off.expense.reference),-off.amount,'expense',off.expense_id)
    s=statement(period)
    for side in('customer','owner','driver'):add(side,line_label('line_due'),s[side]['due'],'statement',period.pk)
    add('operator',line_label('line_profit'),s['operator']['accrual_margin'],'statement',period.pk)
    add('operator',line_label('line_cash_movement'),s['operator']['recorded_cash_movement'],'statement',period.pk)
    add('operator',line_label('line_your_costs'),s['operator']['operator_borne_costs'],'statement',period.pk)
    return rows

def _freeze(settlement,period):
    settlement.lines.all().delete()
    SettlementLine.objects.bulk_create([SettlementLine(settlement=settlement,section=sec,label=lab,amount=amt,source_type=st,source_id=sid,order=i) for i,(sec,lab,amt,st,sid) in enumerate(build_lines(period))])

def _hash(period):
    return fingerprint(statement(period))

def _lock(org,settlement_id):
    s=Settlement.objects.get(pk=settlement_id,organization=org)
    period=Period.objects.select_for_update(of=('self',)).select_related('agreement','organization').get(pk=s.period_id)
    return Settlement.objects.select_for_update().get(pk=s.pk),period

def _blocking(period):
    blocks=[e for e in exceptions_for(period) if e['severity']==BLOCKING]
    if blocks:raise DomainError('close_blocked_by_items',items='; '.join(dict.fromkeys(e['message'] for e in blocks)))

@transaction.atomic
def prepare_settlement(org,user,period_id):
    """Create (or return) the draft settlement for a period. Idempotent."""
    period=Period.objects.select_for_update(of=('self',)).select_related('agreement').get(pk=period_id,organization=org)
    _vehicle_only(period)
    s,created=Settlement.objects.get_or_create(period=period,defaults={'organization':org,'agreement':period.agreement,'month':period.month,'state':Settlement.State.CLOSED if period.closed_at else Settlement.State.DRAFT,'prepared_by':user,'prepared_at':timezone.now()})
    if created:audit(org,user,'settlement.prepare','settlement',s.pk,after={'state':s.state,'month':s.month})
    return s

@transaction.atomic
def request_settlement_approval(org,user,settlement_id):
    s,period=_lock(org,settlement_id)
    if s.state==Settlement.State.CLOSED:raise DomainError('settlement_is_already_closed')
    prior_state=s.state
    _blocking(period)
    _freeze(s,period)
    s.state=Settlement.State.PENDING;s.statement_hash=_hash(period);s.prepared_by=user;s.prepared_at=timezone.now()
    s.approved_by=None;s.approved_at=None
    s.save()
    audit(org,user,'settlement.request_approval','settlement',s.pk,before={'state':prior_state},after={'state':s.state,'statement_hash':s.statement_hash})
    return s

@transaction.atomic
def approve_settlement(org,user,settlement_id,role,*,shortcut=False):
    s,period=_lock(org,settlement_id)
    if s.state==Settlement.State.APPROVED:return s
    if s.state!=Settlement.State.PENDING:raise DomainError('request_approval_first')
    if not shortcut and role not in('admin',get_settings(org).approval_role):raise DomainError('your_role_cannot_approve_settlements_for_this')
    s.state=Settlement.State.APPROVED;s.approved_by=user;s.approved_at=timezone.now();s.save()
    audit(org,user,'settlement.approve','settlement',s.pk,before={'state':'pending_approval'},after={'state':'approved','shortcut':shortcut})
    return s

@transaction.atomic
def close_settlement(org,user,settlement_id):
    """Idempotent: closing again returns the same closed settlement and posts nothing new (notice event keys are unique)."""
    s,period=_lock(org,settlement_id)
    if s.state==Settlement.State.CLOSED:return s
    if s.state!=Settlement.State.APPROVED:raise DomainError('settlement_must_be_approved_before_it_can')
    _blocking(period)
    if _hash(period)!=s.statement_hash:raise DomainError('the_statement_changed_since_approval_request_approval')
    now=timezone.now()
    period.closed_at=now;period.closed_by=user;period.save(update_fields=['closed_at','closed_by'])
    _freeze(s,period)
    s.state=Settlement.State.CLOSED;s.closed_by=user;s.closed_at=now;s.save()
    audit(org,user,'settlement.close','settlement',s.pk,before={'state':'approved'},after={'state':'closed','version':s.version,'period_id':period.pk})
    suffix='' if s.version==1 else f':v{s.version}'
    parties=period_parties(period)
    for side in('owner','customer','driver'):
        notice(org,f'settlement:{period.pk}:{side}{suffix}',parties[side],'settlement_closed',{'period_id':period.pk,'settlement_id':s.pk,'version':s.version})
    return s

@transaction.atomic
def reopen_settlement(org,user,settlement_id,reason):
    """Reopen the latest closed month of an agreement. Refused once its balance was carried forward (reverse that first)."""
    if not reason.strip():raise DomainError('a_reason_is_required_to_reopen_a')
    s,period=_lock(org,settlement_id)
    if s.state!=Settlement.State.CLOSED:raise DomainError('only_closed_settlements_can_be_reopened')
    if Period.objects.filter(agreement=period.agreement,month__gt=period.month,closed_at__isnull=False).exists():raise DomainError('only_the_latest_closed_month_can_be')
    if Charge.objects.filter(period=period,kind='carry_out').exists():raise DomainError('this_month_balance_was_already_carried_forward')
    period.closed_at=None;period.closed_by=None;period.save(update_fields=['closed_at','closed_by'])
    s.state=Settlement.State.REOPENED;s.version+=1;s.reopened_by=user;s.reopened_at=timezone.now();s.reopen_reason=reason.strip()[:240]
    s.approved_by=None;s.approved_at=None;s.statement_hash='';s.save()
    audit(org,user,'settlement.reopen','settlement',s.pk,before={'state':'closed'},after={'state':'reopened','version':s.version},reason=reason)
    return s

def close_period(org,user,period_id):
    """The frontend's single-button close: prepare -> request -> approve (shortcut, no role check beyond the caller's close permission) -> close.
    Idempotent. Legacy error messages are preserved."""
    period=Period.objects.get(pk=period_id,organization=org)
    _vehicle_only(period)
    if period.closed_at:return period
    with transaction.atomic():
        s=prepare_settlement(org,user,period_id)
        request_settlement_approval(org,user,s.pk)
        approve_settlement(org,user,s.pk,'admin',shortcut=True)
        close_settlement(org,user,s.pk)
    period.refresh_from_db();return period
