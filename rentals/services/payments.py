from django.db import transaction
from ..models import Obligation, Payment, Allocation, OwnerOffset, Expense
from .helpers import ZERO, DomainError, bd, total, replay, remember, journal, notice

@transaction.atomic
def record_payment(org,user,data,key):
    existing=replay(org,'payment',key,data,Payment)
    if existing:return existing
    kind=data['kind']
    if kind not in Payment.Kind.values:raise DomainError('Unsupported payment kind')
    amount=bd(data['amount'])
    allocations=data['allocations']
    if not allocations:raise DomainError('Choose at least one obligation')
    if sum((bd(row['amount']) for row in allocations),ZERO)!=amount:raise DomainError('Allocations must equal the payment amount')
    ids=[row['obligation_id'] for row in allocations]
    if len(ids)!=len(set(ids)):raise DomainError('Duplicate obligation allocation')
    obligations=list(Obligation.objects.select_for_update().select_related('period','party').filter(pk__in=ids,period__organization=org).order_by('pk'))
    if len(obligations)!=len(ids):raise DomainError('Obligation does not exist in this organization')
    expected={'customer_payment':Obligation.Kind.CUSTOMER,'owner_payout':Obligation.Kind.OWNER,'driver_payout':Obligation.Kind.DRIVER,'salary_advance':Obligation.Kind.DRIVER}[kind]
    party_id=obligations[0].party_id
    for ob in obligations:
        if ob.kind!=expected or ob.party_id!=party_id or ob.period.closed_at:raise DomainError('Allocation party, type or period is invalid')
        allocation_amount=bd(next(row['amount'] for row in allocations if row['obligation_id']==ob.pk))
        available=ob.amount-total(Allocation.objects.filter(obligation=ob))
        if ob.kind==Obligation.Kind.OWNER:available-=total(OwnerOffset.objects.filter(obligation=ob,expense__state=Expense.State.POSTED))
        if allocation_amount>available:raise DomainError('Payment exceeds an open obligation')
    payment=Payment.objects.create(organization=org,kind=kind,party_id=party_id,amount=amount,reference=data['reference'].strip(),method=data.get('method','bank'),event_date=data['event_date'],recorded_by=user)
    Allocation.objects.bulk_create([Allocation(payment=payment,obligation_id=row['obligation_id'],amount=bd(row['amount'])) for row in allocations])
    if kind=='customer_payment':lines=[('CASH',amount,ZERO),('CUSTOMER_RECEIVABLE',ZERO,amount)]
    elif kind=='owner_payout':lines=[('OWNER_PAYABLE',amount,ZERO),('CASH',ZERO,amount)]
    else:lines=[('DRIVER_PAYABLE',amount,ZERO),('CASH',ZERO,amount)]
    journal(org,'payment',payment.pk,lines)
    notice(org,f'payment:{payment.pk}',payment.party,'payment_recorded',{'payment_id':payment.public_id})
    remember(org,'payment',key,data,payment)
    return payment
