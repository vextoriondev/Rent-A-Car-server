"""Atomic monetary operations; never trust balance fields from the client."""
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .models import (Agreement, Period, Obligation, Expense, Payment, Allocation, OwnerOffset,
    JournalEntry, JournalLine, NotificationOutbox, IdempotencyRecord)

ZERO=Decimal('0.00')
class DomainError(Exception): pass

def bd(value):
    try:
        amount=Decimal(str(value))
        if not amount.is_finite() or amount<=0 or amount!=amount.quantize(Decimal('0.01')): raise ValueError
        return amount
    except (InvalidOperation,ValueError): raise DomainError('Amount must be positive BDT with at most two decimals')

def total(qs,field='amount'):
    return qs.aggregate(value=Sum(field))['value'] or ZERO

def fingerprint(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()

def replay(org,action,key,data,model):
    if not key or len(key)>120: raise DomainError('A valid Idempotency-Key header is required')
    record=IdempotencyRecord.objects.filter(organization=org,action=action,key=key).first()
    if not record:return None
    if record.payload_hash!=fingerprint(data):raise DomainError('Idempotency key reused with a different request')
    return model.objects.get(pk=record.object_id)

def remember(org,action,key,data,obj):
    IdempotencyRecord.objects.create(organization=org,action=action,key=key,payload_hash=fingerprint(data),object_id=obj.pk)

def journal(org,source_type,source_id,lines):
    debit=sum((Decimal(str(d)) for _,d,_ in lines),ZERO)
    credit=sum((Decimal(str(c)) for _,_,c in lines),ZERO)
    if debit!=credit or debit<=0: raise DomainError('Journal is not balanced')
    entry=JournalEntry.objects.create(organization=org,source_type=source_type,source_id=str(source_id))
    JournalLine.objects.bulk_create([JournalLine(entry=entry,account=account,debit=d,credit=c) for account,d,c in lines])
    return entry

def notice(org,event_key,party,event_type,payload):
    NotificationOutbox.objects.get_or_create(organization=org,event_key=event_key,recipient=party,defaults={'event_type':event_type,'payload':payload})

def validate_agreement(agreement):
    org=agreement.organization_id
    if agreement.vehicle.organization_id!=org or agreement.vehicle.owner.organization_id!=org or agreement.customer.organization_id!=org or agreement.driver.organization_id!=org:raise DomainError('Agreement relationships must belong to the organization')
    if agreement.vehicle.owner.kind!='owner' or agreement.customer.kind!='customer' or agreement.driver.kind!='driver':raise DomainError('Party roles do not match agreement')
    if agreement.end_date and agreement.end_date<agreement.start_date:raise DomainError('End date precedes start date')
    if any(x<=0 for x in [agreement.customer_rent,agreement.owner_rent,agreement.driver_salary]):raise DomainError('Monthly terms must be positive')

def locked_period(org,period_id):
    period=Period.objects.select_for_update().select_related('agreement','agreement__vehicle','agreement__customer','agreement__driver','agreement__vehicle__owner').get(pk=period_id,organization=org)
    if period.closed_at: raise DomainError('Period is closed; use an authorized adjustment workflow')
    return period

@transaction.atomic
def issue_period(org,agreement_id,month):
    agreement=Agreement.objects.select_for_update().select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org,active=True)
    if month.day!=1:raise DomainError('Month must be its first day')
    if month<agreement.start_date.replace(day=1) or (agreement.end_date and month>agreement.end_date.replace(day=1)):raise DomainError('Agreement is not effective this month')
    validate_agreement(agreement)
    period,created=Period.objects.get_or_create(organization=org,agreement=agreement,month=month)
    if created:
        for kind,party,amount,lines in [
            (Obligation.Kind.CUSTOMER,agreement.customer,agreement.customer_rent,[('CUSTOMER_RECEIVABLE',agreement.customer_rent,ZERO),('RENT_REVENUE',ZERO,agreement.customer_rent)]),
            (Obligation.Kind.OWNER,agreement.vehicle.owner,agreement.owner_rent,[('OWNER_RENT_EXPENSE',agreement.owner_rent,ZERO),('OWNER_PAYABLE',ZERO,agreement.owner_rent)]),
            (Obligation.Kind.DRIVER,agreement.driver,agreement.driver_salary,[('DRIVER_SALARY_EXPENSE',agreement.driver_salary,ZERO),('DRIVER_PAYABLE',ZERO,agreement.driver_salary)])]:
            ob=Obligation.objects.create(period=period,kind=kind,party=party,amount=amount)
            journal(org,'obligation',ob.pk,lines)
        notice(org,f'period:{period.pk}:customer',agreement.customer,'rent_issued',{'period_id':period.pk})
    return period

def statement(period):
    obligations={o.kind:o for o in period.obligations.all()}
    def figures(kind):
        ob=obligations[kind]
        paid=total(Allocation.objects.filter(obligation=ob))
        return ob,ob.amount,paid
    _,customer,received=figures(Obligation.Kind.CUSTOMER)
    owner_ob,owner,owner_paid=figures(Obligation.Kind.OWNER)
    _,driver,driver_paid=figures(Obligation.Kind.DRIVER)
    offsets=total(OwnerOffset.objects.filter(obligation=owner_ob,expense__state=Expense.State.POSTED))
    operator_cost=total(Expense.objects.filter(period=period,state=Expense.State.POSTED,responsible='operator',paid_by='operator'))
    recoverable=total(Expense.objects.filter(period=period,state=Expense.State.POSTED,paid_by='operator',responsible='owner',offset_owner=False))
    customer_recoverable=total(Expense.objects.filter(period=period,state=Expense.State.POSTED,paid_by='operator',responsible='customer'))
    operator_expenses=total(Expense.objects.filter(period=period,state=Expense.State.POSTED,paid_by='operator'))
    return {'period_id':period.pk,'month':period.month.isoformat(),'closed':bool(period.closed_at),'customer':{'billed':customer,'received':received,'due':customer-received,'separate_recoverable':customer_recoverable},'owner':{'gross':owner,'offsets':offsets,'net':owner-offsets,'paid':owner_paid,'due':owner-offsets-owner_paid,'separate_recoverable':recoverable},'driver':{'earned':driver,'paid':driver_paid,'due':driver-driver_paid},'operator':{'accrual_margin':customer-owner-driver-operator_cost,'recorded_cash_movement':received-owner_paid-driver_paid-operator_expenses,'operator_borne_costs':operator_cost}}

def post_expense_locked(expense,approver=None):
    p=expense.period
    if p.closed_at:raise DomainError('Period is closed')
    if expense.state==Expense.State.POSTED:return expense
    if expense.state==Expense.State.REVERSED:raise DomainError('Cannot post a reversed expense')
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
        lines=[(debit_account,expense.amount,ZERO),('CASH',ZERO,expense.amount)]
        if expense.offset_owner:lines += [('OWNER_PAYABLE',expense.amount,ZERO),('OWNER_RECOVERABLE',ZERO,expense.amount)]
        journal(p.organization,'expense',expense.pk,lines)
    notice(p.organization,f'expense:{expense.pk}:owner',p.agreement.vehicle.owner,'expense_posted',{'expense_id':expense.public_id}) if expense.responsible=='owner' else None
    return expense

@transaction.atomic
def record_expense(org,user,data,key):
    existing=replay(org,'expense',key,data,Expense)
    if existing:return existing
    p=locked_period(org,data['period_id'])
    amount=bd(data['amount'])
    category=data['category'].lower()
    rule={'fuel':p.agreement.fuel_responsible,'repair':p.agreement.repair_responsible}.get(category)
    if category not in ('fuel','repair','toll','maintenance','cleaning','other'):raise DomainError('Unsupported expense category')
    liable=data['responsible']
    if liable not in ('owner','operator','customer'):raise DomainError('Unsupported responsible party')
    if rule and liable!=rule:raise DomainError('Agreement expense rule differs; create an approved revision first')
    paid_by=data['paid_by']
    if paid_by not in ('operator','owner'):raise DomainError('This slice supports operator or owner funded expenses')
    if paid_by=='owner' and liable!='owner':raise DomainError('Cross-party owner-funded expenses need a reimbursement workflow')
    offset=bool(data.get('offset_owner'))
    if offset and not (liable=='owner' and paid_by=='operator' and p.agreement.owner_offset_allowed):raise DomainError('Owner offset is not authorized')
    if not data['reference'].strip():raise DomainError('Reference is required')
    expense=Expense.objects.create(period=p,amount=amount,event_date=data.get('event_date',p.month),category=category,paid_by=paid_by,responsible=liable,offset_owner=offset,reference=data['reference'].strip(),description=data.get('description',''),recorded_by=user,rule_snapshot={'fuel_responsible':p.agreement.fuel_responsible,'repair_responsible':p.agreement.repair_responsible,'owner_offset_allowed':p.agreement.owner_offset_allowed})
    if amount<Decimal('5000.00'):post_expense_locked(expense)
    remember(org,'expense',key,data,expense)
    return expense

@transaction.atomic
def approve_expense(org,user,expense_id):
    expense=Expense.objects.select_for_update().select_related('period','period__agreement','period__agreement__vehicle__owner').get(pk=expense_id,period__organization=org)
    locked_period(org,expense.period_id)
    return post_expense_locked(expense,user)

@transaction.atomic
def reverse_expense(org,user,expense_id,reason):
    if not reason.strip():raise DomainError('Reversal reason is required')
    expense=Expense.objects.select_for_update().select_related('period').get(pk=expense_id,period__organization=org)
    locked_period(org,expense.period_id)
    if expense.state!=Expense.State.POSTED:raise DomainError('Only posted expenses can be reversed')
    original=JournalEntry.objects.filter(organization=org,source_type='expense',source_id=str(expense.pk)).first()
    if original:journal(org,'expense_reversal',expense.pk,[(line.account,line.credit,line.debit) for line in original.lines.all()])
    expense.state=Expense.State.REVERSED
    expense.reversed_by=user
    expense.reversed_at=timezone.now()
    expense.reversal_reason=reason.strip()
    expense.save(update_fields=['state','reversed_by','reversed_at','reversal_reason'])
    return expense

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

@transaction.atomic
def close_period(org,user,period_id):
    period=Period.objects.select_for_update().get(pk=period_id,organization=org)
    if period.closed_at:return period
    if period.expenses.filter(state=Expense.State.PENDING).exists():raise DomainError('Pending expense approval blocks close')
    s=statement(period)
    if any(v<0 for v in [s['customer']['due'],s['owner']['due'],s['driver']['due']]):raise DomainError('Negative balance blocks close')
    period.closed_at=timezone.now();period.closed_by=user
    period.save(update_fields=['closed_at','closed_by'])
    notice(org,f'settlement:{period.pk}:owner',period.agreement.vehicle.owner,'settlement_closed',{'period_id':period.pk})
    notice(org,f'settlement:{period.pk}:customer',period.agreement.customer,'settlement_closed',{'period_id':period.pk})
    notice(org,f'settlement:{period.pk}:driver',period.agreement.driver,'settlement_closed',{'period_id':period.pk})
    return period
