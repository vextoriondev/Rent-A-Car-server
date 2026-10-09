from django.db import transaction
from ..models import Obligation, Payment, Allocation, OwnerOffset, Expense
from .statements import statement
from .audit import audit
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
    obligations=list(Obligation.objects.select_for_update(of=('self',)).select_related('period','party').filter(pk__in=ids,period__organization=org).order_by('pk'))
    if len(obligations)!=len(ids):raise DomainError('Obligation does not exist in this organization')
    side={'customer_payment':'customer','owner_payout':'owner','driver_payout':'driver','salary_advance':'driver'}[kind]
    party_id=obligations[0].party_id
    for ob in obligations:
        if ob.period.closed_at:raise DomainError('Period is closed; carry its balance forward and record the payment against the new opening balance')
        if ob.party.kind!=side or ob.kind==Obligation.Kind.CARRY_OUT or ob.party_id!=party_id:raise DomainError('Allocation party, type or period is invalid')
        allocation_amount=bd(next(row['amount'] for row in allocations if row['obligation_id']==ob.pk))
        available=ob.amount-total(Allocation.objects.filter(obligation=ob))
        if ob.kind==Obligation.Kind.OWNER:available-=total(OwnerOffset.objects.filter(obligation=ob,expense__state=Expense.State.POSTED))
        if allocation_amount>available:raise DomainError('Payment exceeds an open obligation')
    # credits, offsets and carried balances reduce what is owed below the sum of the obligations, so also cap per period by the ledger due
    for period in {ob.period_id:ob.period for ob in obligations}.values():
        part=sum((bd(row['amount']) for row in allocations if next(o for o in obligations if o.pk==row['obligation_id']).period_id==period.pk),ZERO)
        if part>statement(period)[side]['due']:raise DomainError('Payment exceeds an open obligation')
    payment=Payment.objects.create(organization=org,kind=kind,party_id=party_id,amount=amount,reference=data['reference'].strip(),method=data.get('method','bank'),event_date=data['event_date'],recorded_by=user)
    Allocation.objects.bulk_create([Allocation(payment=payment,obligation_id=row['obligation_id'],amount=bd(row['amount'])) for row in allocations])
    by_id={ob.pk:ob for ob in obligations}
    lines=[]
    for row in allocations:   # one pair of lines per allocation so each carries its own period and party dimension
        part=bd(row['amount']);ob=by_id[row['obligation_id']]
        if kind=='customer_payment':lines+=[('CASH',part,ZERO,None,ob.period),('CUSTOMER_RECEIVABLE',ZERO,part,payment.party,ob.period)]
        elif kind=='owner_payout':lines+=[('OWNER_PAYABLE',part,ZERO,payment.party,ob.period),('CASH',ZERO,part,None,ob.period)]
        else:lines+=[('DRIVER_PAYABLE',part,ZERO,payment.party,ob.period),('CASH',ZERO,part,None,ob.period)]
    journal(org,'payment_advance' if kind=='salary_advance' else 'payment',payment.pk,lines)
    notice(org,f'payment:{payment.pk}',payment.party,'payment_recorded',{'payment_id':payment.public_id})
    remember(org,'payment',key,data,payment)
    audit(org,user,'payment.record','payment',payment.pk,after={'public_id':payment.public_id,'kind':kind,'amount':amount,'party_id':payment.party_id,'reference':payment.reference,'allocations':[{'obligation_id':r['obligation_id'],'amount':r['amount']} for r in allocations]})
    return payment
