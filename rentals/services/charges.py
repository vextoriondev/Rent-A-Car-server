"""Variable charges, credits, adjustments, reimbursements, opening balances and period carry-forward.

A charge is posted against one *side* (customer/owner/driver) of a period. It creates one signed Obligation
(increase = +, decrease = -) and one balanced journal entry; it is never edited. Direction means "what the party is
owed/owes goes up/down": customer increase = owes more; owner/driver increase = we owe more."""
from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction
from ..models import Charge, Obligation, Period, IdempotencyRecord
from .audit import audit
from .helpers import ZERO, DomainError, bd, replay, remember, journal, period_parties
from .statements import statement

SIDES=('customer','owner','driver')
KINDS={'overtime':{'increase'},'absence_deduction':{'decrease'},'fine':{'increase','decrease'},'charge':{'increase'},'credit':{'decrease'},'adjustment':{'increase','decrease'},'reimbursement':{'increase','decrease'},'expense_front':{'increase','decrease'},'opening_balance':{'increase','decrease'},'carry_out':{'increase','decrease'}}
EXTRA_KINDS={'overtime','absence_deduction','fine'}
EXTRA_SIDES={'overtime':('customer',),'absence_deduction':('driver',),'fine':('customer','driver')}
CONTRA={'overtime':'OTHER_REVENUE','fine':'OTHER_REVENUE','charge':'OTHER_REVENUE','credit':'OTHER_REVENUE','adjustment':'OTHER_REVENUE','opening_balance':'OPENING_BALANCE','carry_out':'OPENING_BALANCE'}
OWNER_DRIVER_CONTRA={'owner':'OWNER_RENT_EXPENSE','driver':'DRIVER_SALARY_EXPENSE'}
SIDE_ACCOUNT={'customer':'CUSTOMER_RECEIVABLE','owner':'OWNER_PAYABLE','driver':'DRIVER_PAYABLE'}

def _contra(kind,side,direction):
    if kind=='reimbursement':
        if side=='customer' and direction=='increase':return 'CUSTOMER_RECOVERABLE'
        if side=='owner' and direction=='decrease':return 'OWNER_RECOVERABLE'
        if side=='driver' and direction=='decrease':return 'DRIVER_RECOVERABLE'
        raise DomainError('Reimbursements bill a customer (increase) or reduce an owner or driver payable (decrease)')
    if kind in('opening_balance','carry_out'):return 'OPENING_BALANCE'
    return CONTRA[kind] if side=='customer' else OWNER_DRIVER_CONTRA[side]   # a driver's absence deduction or fine lowers the salary expense

def _post(org,user,period,side,kind,direction,amount,description='',reference='',source=('',''),contra=None,contra_party=None,quantity=None,unit_rate=None,event_date=None):
    """`contra`/`contra_party` let an expense paid by a third party choose the other side of the entry (the responsible party's account)."""
    parties=period_parties(period)
    party=parties[side]
    if party is None:raise DomainError(f'This agreement has no {side}')
    if kind not in KINDS or direction not in KINDS[kind]:raise DomainError(f'A {kind} cannot be an {direction}')
    contra=contra or _contra(kind,side,direction)
    if kind=='reimbursement':
        stmt=statement(period)[side]
        if amount>stmt['separate_recoverable']:raise DomainError('Reimbursement exceeds the recoverable balance')
    signed=amount if direction=='increase' else -amount
    charge=Charge.objects.create(organization=org,kind=kind,direction=direction,side=side,party=party,agreement=period.agreement,period=period,amount=amount,description=description,reference=reference,quantity=quantity,unit_rate=unit_rate,source_type=source[0],source_id=source[1],created_by=user)
    ob=Obligation.objects.create(period=period,kind=kind,party=party,amount=signed)
    charge.obligation=ob;charge.save(update_fields=['obligation'])
    account=SIDE_ACCOUNT[side]
    debit_side=(side=='customer')==(direction=='increase')
    lines=[(account,amount,ZERO,party),(contra,ZERO,amount,contra_party)] if debit_side else [(contra,amount,ZERO,contra_party),(account,ZERO,amount,party)]
    when=event_date or min(max(date.today(),period.month),(period.month.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1))   # no date of its own: the posting day, kept inside its month
    journal(org,'carry_out' if kind=='carry_out' else 'charge',charge.pk,lines,period=period,event_date=when)
    audit(org,user,'charge.post' if kind!='carry_out' else 'period.carry_out','charge',charge.pk,after={'kind':kind,'side':side,'direction':direction,'amount':amount,'period_id':period.pk,'description':description,'quantity':quantity,'unit_rate':unit_rate})
    return charge

def charge_suggestion(period,kind,quantity):
    """Rate and amount suggested for an overtime or absence charge; the user may overwrite them. unit_rate is None when no rate is known."""
    quantity=bd(quantity)
    if kind=='overtime':
        if not period.agreement_id:raise DomainError('Overtime applies to vehicle agreements')
        from .terms import version_for
        v=period.agreement_version or version_for(period.agreement,period.month)
        rate=v.overtime_rate;unit=v.overtime_unit or 'unit'
    elif kind=='absence_deduction':
        salary=Obligation.objects.filter(period=period,kind='driver_salary').values_list('amount',flat=True).first()
        if not salary:return {'kind':kind,'quantity':quantity,'unit':'day','unit_rate':None,'amount':None}
        days=(period.month.replace(day=28)+timedelta(days=4)).replace(day=1)-period.month
        rate=(salary/days.days).quantize(Decimal('0.01'));unit='day'   # replaced by the M12 daily rate once agreements are date based
    else:raise DomainError('Suggestions exist for overtime and absence deductions only')
    return {'kind':kind,'quantity':quantity,'unit':unit,'unit_rate':rate,'amount':(rate*quantity).quantize(Decimal('0.01')) if rate is not None else None}

@transaction.atomic
def post_charge(org,user,data,key,can_approve=False):
    """Post one charge/credit/adjustment/reimbursement/opening balance to an open period. Idempotent per key."""
    existing=replay(org,'charge',key,data,Charge)
    if existing:return existing
    kind=data['kind']
    if kind=='carry_out':raise DomainError('Carry-forward is created by carry_forward only')
    side=data['side']
    if side not in SIDES:raise DomainError('Unknown side')
    period=Period.objects.select_for_update(of=('self',)).select_related('agreement').get(pk=data['period_id'],organization=org)
    if period.closed_at:raise DomainError('Period is closed; use an authorized adjustment workflow')
    default={'charge':'increase','credit':'decrease','overtime':'increase','absence_deduction':'decrease'}.get(kind)
    direction=data.get('direction') or default
    if kind=='fine':direction='increase' if side=='customer' else 'decrease'   # a customer's fine adds to their bill, a driver's is taken from salary
    if not direction:raise DomainError('Direction is required for this kind')
    quantity=unit_rate=None;amount=data.get('amount')
    if kind in EXTRA_KINDS:
        if side not in EXTRA_SIDES[kind]:raise DomainError({'overtime':'Overtime is charged to the customer','absence_deduction':'An absence deduction comes off the driver salary','fine':'A fine is charged to the customer or the driver'}[kind])
        if not (data.get('description') or '').strip():raise DomainError('A reason is required')
        quantity=Decimal(str(data['quantity'])) if data.get('quantity') is not None else None;unit_rate=bd(data['unit_rate']).quantize(Decimal('0.01')) if data.get('unit_rate') is not None else None
        if quantity is not None and (not quantity.is_finite() or quantity<=0 or quantity!=quantity.quantize(Decimal('0.01'))):raise DomainError('The quantity must be above zero, with at most two decimals')
        if quantity is not None:quantity=quantity.quantize(Decimal('0.01'))
        if quantity is not None and unit_rate is not None:
            if amount is not None and bd(amount)!=(quantity*unit_rate).quantize(Decimal('0.01')):raise DomainError('The amount must equal quantity x rate')
            amount=(quantity*unit_rate).quantize(Decimal('0.01'))
        if amount is None:raise DomainError('Give an amount, or a quantity and a rate')
        threshold=getattr(period.agreement_version,'approval_threshold',Decimal('5000.00'))
        if bd(amount)>=threshold and not can_approve:raise DomainError(f'Charges of {threshold} or more need approval by an administrator')
    elif amount is None:raise DomainError('An amount is required')
    charge=_post(org,user,period,side,kind,direction,bd(amount),data.get('description',''),data.get('reference',''),quantity=quantity,unit_rate=unit_rate)
    remember(org,'charge',key,data,charge)
    return charge

@transaction.atomic
def carry_forward(org,user,period_id):
    """Move what is still due on the previous (closed) month into this month as opening balances.

    Per side with a non-zero due it posts a carry_out in the previous period (its due becomes 0, shown as carried_forward)
    and an opening_balance in this one, so the party's total never changes. Idempotent; locks both periods in pk order."""
    target=Period.objects.select_related('agreement').get(pk=period_id,organization=org)
    prev_month=(target.month-timedelta(days=1)).replace(day=1)
    prior=Period.objects.filter(organization=org,agreement=target.agreement,driver_agreement=target.driver_agreement,month=prev_month).first()
    if not prior:raise DomainError('There is no previous period to carry forward from')
    locked={p.pk:p for p in Period.objects.select_for_update(of=('self',)).select_related('agreement').filter(pk__in=[prior.pk,target.pk]).order_by('pk')}
    prior,target=locked[prior.pk],locked[target.pk]
    if not prior.closed_at:raise DomainError('Close the previous period before carrying its balance forward')
    if target.closed_at:raise DomainError('Period is closed')
    posted=[]
    for side in SIDES:
        due=statement(prior)[side]['due']
        if due==0:continue
        out_dir='decrease' if due>0 else 'increase';in_dir='increase' if due>0 else 'decrease'
        key=f'{prior.pk}:{side}'
        if Charge.objects.filter(organization=org,source_type='carry_forward_out',source_id=key).exists():continue
        amount=abs(due)
        posted.append(_post(org,user,prior,side,'carry_out',out_dir,amount,f'Carried to {target.month:%Y-%m}',source=('carry_forward_out',key),event_date=target.month))
        posted.append(_post(org,user,target,side,'opening_balance',in_dir,amount,f'Carried from {prior.month:%Y-%m}',source=('carry_forward_in',key),event_date=target.month))
    return posted
