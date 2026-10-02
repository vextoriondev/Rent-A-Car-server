"""Variable charges, credits, adjustments, reimbursements, opening balances and period carry-forward.

A charge is posted against one *side* (customer/owner/driver) of a period. It creates one signed Obligation
(increase = +, decrease = -) and one balanced journal entry; it is never edited. Direction means "what the party is
owed/owes goes up/down": customer increase = owes more; owner/driver increase = we owe more."""
from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction
from ..models import Charge, Obligation, Period, IdempotencyRecord
from .helpers import ZERO, DomainError, bd, replay, remember, journal, period_parties
from .statements import statement

SIDES=('customer','owner','driver')
KINDS={'charge':{'increase'},'credit':{'decrease'},'adjustment':{'increase','decrease'},'reimbursement':{'increase','decrease'},'opening_balance':{'increase','decrease'},'carry_out':{'increase','decrease'}}
CONTRA={'charge':'OTHER_REVENUE','credit':'OTHER_REVENUE','adjustment':'OTHER_REVENUE','opening_balance':'OPENING_BALANCE','carry_out':'OPENING_BALANCE'}
OWNER_DRIVER_CONTRA={'owner':'OWNER_RENT_EXPENSE','driver':'DRIVER_SALARY_EXPENSE'}
SIDE_ACCOUNT={'customer':'CUSTOMER_RECEIVABLE','owner':'OWNER_PAYABLE','driver':'DRIVER_PAYABLE'}

def _contra(kind,side,direction):
    if kind=='reimbursement':
        if side=='customer' and direction=='increase':return 'CUSTOMER_RECOVERABLE'
        if side=='owner' and direction=='decrease':return 'OWNER_RECOVERABLE'
        raise DomainError('Reimbursements bill a customer (increase) or reduce an owner payable (decrease); driver recoveries arrive with the payments milestone')
    if kind in('opening_balance','carry_out'):return 'OPENING_BALANCE'
    return CONTRA[kind] if side=='customer' else OWNER_DRIVER_CONTRA[side]

def _post(org,user,period,side,kind,direction,amount,description='',reference='',source=('','')):
    parties=period_parties(period)
    party=parties[side]
    if kind not in KINDS or direction not in KINDS[kind]:raise DomainError(f'A {kind} cannot be an {direction}')
    contra=_contra(kind,side,direction)
    if kind=='reimbursement':
        stmt=statement(period)[side]
        if amount>stmt['separate_recoverable']:raise DomainError('Reimbursement exceeds the recoverable balance')
    signed=amount if direction=='increase' else -amount
    charge=Charge.objects.create(organization=org,kind=kind,direction=direction,side=side,party=party,agreement=period.agreement,period=period,amount=amount,description=description,reference=reference,source_type=source[0],source_id=source[1],created_by=user)
    ob=Obligation.objects.create(period=period,kind=kind,party=party,amount=signed)
    charge.obligation=ob;charge.save(update_fields=['obligation'])
    account=SIDE_ACCOUNT[side]
    debit_side=(side=='customer')==(direction=='increase')
    lines=[(account,amount,ZERO,party),(contra,ZERO,amount)] if debit_side else [(contra,amount,ZERO),(account,ZERO,amount,party)]
    journal(org,'carry_out' if kind=='carry_out' else 'charge',charge.pk,lines,period=period)
    return charge

@transaction.atomic
def post_charge(org,user,data,key):
    """Post one charge/credit/adjustment/reimbursement/opening balance to an open period. Idempotent per key."""
    existing=replay(org,'charge',key,data,Charge)
    if existing:return existing
    kind=data['kind']
    if kind=='carry_out':raise DomainError('Carry-forward is created by carry_forward only')
    side=data['side']
    if side not in SIDES:raise DomainError('Unknown side')
    period=Period.objects.select_for_update().select_related('agreement').get(pk=data['period_id'],organization=org)
    if period.closed_at:raise DomainError('Period is closed; use an authorized adjustment workflow')
    default={'charge':'increase','credit':'decrease'}.get(kind)
    direction=data.get('direction') or default
    if not direction:raise DomainError('Direction is required for this kind')
    charge=_post(org,user,period,side,kind,direction,bd(data['amount']),data.get('description',''),data.get('reference',''))
    remember(org,'charge',key,data,charge)
    return charge

@transaction.atomic
def carry_forward(org,user,period_id):
    """Move what is still due on the previous (closed) month into this month as opening balances.

    Per side with a non-zero due it posts a carry_out in the previous period (its due becomes 0, shown as carried_forward)
    and an opening_balance in this one, so the party's total never changes. Idempotent; locks both periods in pk order."""
    target=Period.objects.select_related('agreement').get(pk=period_id,organization=org)
    prev_month=(target.month-timedelta(days=1)).replace(day=1)
    prior=Period.objects.filter(organization=org,agreement=target.agreement,month=prev_month).first()
    if not prior:raise DomainError('There is no previous period to carry forward from')
    locked={p.pk:p for p in Period.objects.select_for_update().select_related('agreement').filter(pk__in=[prior.pk,target.pk]).order_by('pk')}
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
        posted.append(_post(org,user,prior,side,'carry_out',out_dir,amount,f'Carried to {target.month:%Y-%m}',source=('carry_forward_out',key)))
        posted.append(_post(org,user,target,side,'opening_balance',in_dir,amount,f'Carried from {prior.month:%Y-%m}',source=('carry_forward_in',key)))
    return posted
