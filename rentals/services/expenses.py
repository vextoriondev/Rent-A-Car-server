"""Expense engine: rule resolution (by the version the period was issued under), draft -> pending -> posted -> reversed,
approvals, per-entry override, duplicate warning and proof-required enforcement."""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from ..models import Period, AgreementVersion, Approval, Expense, ExpenseCategory, ExpenseRule, JournalEntry, Obligation, OwnerOffset
from .helpers import ZERO, DomainError, bd, replay, remember, journal, notice, locked_period, period_parties
from .statements import statement

DEFAULT_CATEGORIES=[('fuel','Fuel / gas'),('toll','Toll / parking'),('maintenance','Routine maintenance'),('repair','Repairs'),('damage','Damage'),('cleaning','Cleaning'),('driver_allowance','Driver allowance'),('other','Miscellaneous')]
PARTIES=('owner','operator','customer')
DEFAULT_THRESHOLD=Decimal('5000.00')

def period_terms(period):
    """Terms the period was issued under (falls back to the agreement itself for rows issued before versions existed)."""
    return period.agreement_version or period.agreement

def ensure_categories(org):
    for code,name in DEFAULT_CATEGORIES:ExpenseCategory.objects.get_or_create(organization=org,code=code,defaults={'name':name})

def create_category(org,code,name):
    code=code.strip().lower().replace(' ','_')
    if not code or not name.strip():raise DomainError('Code and name are required')
    ensure_categories(org)
    cat,created=ExpenseCategory.objects.get_or_create(organization=org,code=code,defaults={'name':name.strip()})
    if not created:raise DomainError('Category already exists')
    return cat

def resolve_rule(period,category):
    """The rule that applied: an explicit ExpenseRule of the period's version, else the version's fuel/repair columns, else no constraint."""
    terms=period_terms(period)
    rule=ExpenseRule.objects.filter(agreement_version=terms,category=category).first() if isinstance(terms,AgreementVersion) else None
    threshold=getattr(terms,'approval_threshold',DEFAULT_THRESHOLD)
    if rule:
        return {'rule':rule,'responsible':rule.responsible,'first_payer':rule.first_payer,'deductible_from':rule.deductible_from,'proof_required':rule.proof_required,'threshold':rule.approval_threshold or threshold,'source':'rule'}
    legacy={'fuel':terms.fuel_responsible,'repair':terms.repair_responsible}.get(category)
    return {'rule':None,'responsible':legacy,'first_payer':None,'deductible_from':None,'proof_required':False,'threshold':threshold,'source':'version' if legacy else 'none'}

def _prepare(org,p,data,can_override,exclude_pk=None):
    """Validate one entry against the period's rule; returns (model fields, resolved rule, overridden?)."""
    ensure_categories(org)
    amount=bd(data['amount'])
    category=data['category'].strip().lower()
    cat=ExpenseCategory.objects.filter(organization=org,code=category,active=True).first()
    if not cat:raise DomainError('Unsupported expense category')
    liable=data['responsible']
    if liable not in PARTIES:raise DomainError('Unsupported responsible party')
    paid_by=data['paid_by']
    if paid_by not in ('operator','owner'):raise DomainError('This slice supports operator or owner funded expenses')
    if paid_by=='owner' and liable!='owner':raise DomainError('Cross-party owner-funded expenses need a reimbursement workflow')
    reference=data['reference'].strip()
    if not reference:raise DomainError('Reference is required')
    resolved=resolve_rule(p,category)
    override_reason=(data.get('override_reason') or '').strip()
    overridden=False
    if resolved['responsible'] and liable!=resolved['responsible']:
        if not override_reason:raise DomainError('Agreement expense rule differs; create an approved revision first')
        if not can_override:raise DomainError('Overriding the agreement rule requires permission')
        overridden=True
    offset=bool(data.get('offset_owner'))
    terms=period_terms(p)
    deductible=resolved['deductible_from']!='none'
    if offset and not (liable=='owner' and paid_by=='operator' and terms.owner_offset_allowed and deductible):raise DomainError('Owner offset is not authorized')
    duplicate_reason=(data.get('allow_duplicate_reason') or '').strip()
    event_date=data.get('event_date',p.month)
    clones=Expense.objects.filter(period__organization=org).exclude(state=Expense.State.REVERSED).exclude(pk=exclude_pk)
    twin=clones.filter(period__agreement=p.agreement,amount=amount,event_date=event_date,category=category).first() or clones.filter(reference=reference).exclude(period=p).first()
    if twin and not duplicate_reason:raise DomainError(f'Possible duplicate of {twin.public_id}; give allow_duplicate_reason to record it anyway')
    fields={'category':category,'category_ref':cat,'amount':amount,'event_date':event_date,'paid_by':paid_by,'responsible':liable,'offset_owner':offset,'reference':reference,'description':data.get('description',''),'receipt_reference':(data.get('receipt_reference') or '').strip(),'rule':resolved['rule'],
        'override_reason':override_reason if overridden else '','duplicate_reason':duplicate_reason if twin else '',
        'rule_snapshot':{'source':resolved['source'],'rule_id':resolved['rule'].pk if resolved['rule'] else None,'category':category,'responsible':resolved['responsible'],'first_payer':resolved['first_payer'],'deductible_from':resolved['deductible_from'],'proof_required':resolved['proof_required'],'approval_threshold':str(resolved['threshold']),
            'fuel_responsible':terms.fuel_responsible,'repair_responsible':terms.repair_responsible,'owner_offset_allowed':terms.owner_offset_allowed,'agreement_version':getattr(terms,'number',None)}}
    return fields,resolved,overridden

def _require_proof(fields,resolved):
    if resolved['proof_required'] and not fields['receipt_reference']:raise DomainError('A receipt reference is required for this category')

def _route(expense,user,resolved,overridden):
    """Below threshold (and not an override) posts immediately; otherwise it waits for an approval record."""
    expense.state=Expense.State.PENDING;expense.save(update_fields=['state'])
    if expense.amount<resolved['threshold'] and not overridden:
        post_expense_locked(expense);return expense
    Approval.objects.get_or_create(organization=expense.period.organization,subject_type='expense',subject_id=str(expense.pk),defaults={'requested_by':user,'threshold_at_time':resolved['threshold'],'reason':expense.override_reason})
    return expense

def post_expense_locked(expense,approver=None):
    p=expense.period
    if p.closed_at:raise DomainError('Period is closed')
    if expense.state==Expense.State.POSTED:return expense
    if expense.state==Expense.State.REVERSED:raise DomainError('Cannot post a reversed expense')
    if expense.state==Expense.State.DRAFT:raise DomainError('Submit the draft before posting it')
    if expense.offset_owner:
        owner_ob=Obligation.objects.select_for_update().get(period=p,kind=Obligation.Kind.OWNER)
        current=statement(p)['owner']['due']
        if expense.amount>current:raise DomainError('Offset exceeds unpaid owner balance')
        OwnerOffset.objects.create(expense=expense,obligation=owner_ob,amount=expense.amount)
    expense.state=Expense.State.POSTED
    expense.approved_by=approver
    expense.save(update_fields=['state','approved_by'])
    if expense.paid_by=='operator':
        debit_account={'operator':'OPERATING_EXPENSE','owner':'OWNER_RECOVERABLE','customer':'CUSTOMER_RECOVERABLE'}[expense.responsible]
        parties=period_parties(p)
        debit_party={'owner':parties['owner'],'customer':parties['customer']}.get(expense.responsible)
        lines=[(debit_account,expense.amount,ZERO,debit_party),('CASH',ZERO,expense.amount)]
        if expense.offset_owner:lines += [('OWNER_PAYABLE',expense.amount,ZERO,parties['owner']),('OWNER_RECOVERABLE',ZERO,expense.amount,parties['owner'])]
        journal(p.organization,'expense',expense.pk,lines,period=p)
    notice(p.organization,f'expense:{expense.pk}:owner',p.obligations.get(kind=Obligation.Kind.OWNER).party,'expense_posted',{'expense_id':expense.public_id}) if expense.responsible=='owner' else None
    return expense

def _target_period(org,data):
    """The period an entry posts to. A closed period never takes the entry silently: with late_entry=true it goes to the next
    open period of the agreement as a linked adjustment (adjusts_period), otherwise it is refused with instructions."""
    period=Period.objects.select_for_update(of=('self',)).select_related('agreement','agreement_version').get(pk=data['period_id'],organization=org)
    if not period.closed_at:return period,None
    nxt=(period.month.replace(day=28)+timedelta(days=4)).replace(day=1)
    if not data.get('late_entry'):raise DomainError(f'Period is closed; resubmit with late_entry=true to post it as an adjustment in {nxt:%Y-%m}')
    target=Period.objects.select_for_update(of=('self',)).select_related('agreement','agreement_version').filter(organization=org,agreement=period.agreement,month=nxt).first()
    if not target:raise DomainError(f'Issue {nxt:%Y-%m} first, then post the late entry as an adjustment there')
    if target.closed_at:raise DomainError(f'{nxt:%Y-%m} is closed too; reopen it or post the entry in a later open period')
    return target,period

@transaction.atomic
def record_expense(org,user,data,key,can_override=False):
    """Record (or, with data['draft'], save a draft of) one expense. Idempotent per key."""
    existing=replay(org,'expense',key,data,Expense)
    if existing:return existing
    p,adjusts=_target_period(org,data)
    fields,resolved,overridden=_prepare(org,p,data,can_override)
    if adjusts:fields['description']=f"Late entry for {adjusts.month:%Y-%m}: {fields['description']}".strip()
    draft=bool(data.get('draft'))
    if not draft:_require_proof(fields,resolved)
    expense=Expense.objects.create(period=p,adjusts_period=adjusts,recorded_by=user,state=Expense.State.DRAFT if draft else Expense.State.PENDING,overridden_by=user if overridden else None,**fields)
    if not draft:_route(expense,user,resolved,overridden)
    remember(org,'expense',key,data,expense)
    return expense

def _locked_draft(org,expense_id):
    expense=Expense.objects.select_for_update().select_related('period').get(pk=expense_id,period__organization=org)
    if expense.state!=Expense.State.DRAFT:raise DomainError('Only drafts can be changed; posted entries are corrected by reversal')
    return expense,locked_period(org,expense.period_id)

@transaction.atomic
def update_draft(org,user,expense_id,data,can_override=False):
    expense,p=_locked_draft(org,expense_id)
    merged={'amount':expense.amount,'category':expense.category,'responsible':expense.responsible,'paid_by':expense.paid_by,'reference':expense.reference,'event_date':expense.event_date,'offset_owner':expense.offset_owner,'description':expense.description,'receipt_reference':expense.receipt_reference,'override_reason':expense.override_reason,'allow_duplicate_reason':expense.duplicate_reason}
    merged.update({k:v for k,v in data.items() if v is not None and k!='period_id'})
    fields,_,overridden=_prepare(org,p,merged,can_override,exclude_pk=expense.pk)
    for k,v in fields.items():setattr(expense,k,v)
    expense.overridden_by=user if overridden else None
    expense.save();return expense

@transaction.atomic
def submit_expense(org,user,expense_id):
    """draft -> posted (below threshold) or pending approval. Re-validates against the period's rule at submit time."""
    expense,p=_locked_draft(org,expense_id)
    data={'amount':expense.amount,'category':expense.category,'responsible':expense.responsible,'paid_by':expense.paid_by,'reference':expense.reference,'event_date':expense.event_date,'offset_owner':expense.offset_owner,'description':expense.description,'receipt_reference':expense.receipt_reference,'override_reason':expense.override_reason,'allow_duplicate_reason':expense.duplicate_reason}
    fields,resolved,overridden=_prepare(org,p,data,can_override=bool(expense.overridden_by_id),exclude_pk=expense.pk)
    _require_proof(fields,resolved)
    for k,v in fields.items():setattr(expense,k,v)
    expense.save()
    return _route(expense,user,resolved,overridden)

@transaction.atomic
def discard_draft(org,user,expense_id):
    expense,_=_locked_draft(org,expense_id)
    expense.delete()

@transaction.atomic
def approve_expense(org,user,expense_id):
    expense=Expense.objects.select_for_update().select_related('period','period__agreement','period__agreement__vehicle__owner').get(pk=expense_id,period__organization=org)
    locked_period(org,expense.period_id)
    posted=post_expense_locked(expense,user)
    Approval.objects.filter(organization=org,subject_type='expense',subject_id=str(expense.pk),decision=Approval.Decision.PENDING).update(decision=Approval.Decision.APPROVED,decided_by=user,decided_at=timezone.now())
    return posted

@transaction.atomic
def reverse_expense(org,user,expense_id,reason):
    """The original keeps its amount and links to a mirror-image journal entry; balances are derived from POSTED rows only."""
    if not reason.strip():raise DomainError('Reversal reason is required')
    expense=Expense.objects.select_for_update().select_related('period').get(pk=expense_id,period__organization=org)
    locked_period(org,expense.period_id)
    if expense.state!=Expense.State.POSTED:raise DomainError('Only posted expenses can be reversed')
    original=JournalEntry.objects.filter(organization=org,source_type='expense',source_id=str(expense.pk)).first()
    if original:journal(org,'expense_reversal',expense.pk,[(line.account,line.credit,line.debit,line.party,line.period) for line in original.lines.select_related('party','period')])
    expense.state=Expense.State.REVERSED
    expense.reversed_by=user
    expense.reversed_at=timezone.now()
    expense.reversal_reason=reason.strip()
    expense.save(update_fields=['state','reversed_by','reversed_at','reversal_reason'])
    return expense
