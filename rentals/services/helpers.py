"""Shared building blocks for the domain services: money parsing, idempotency replay and the balanced journal."""
import hashlib
import json
from decimal import Decimal, InvalidOperation  # noqa
from django.db import transaction
from django.db.models import Q
from ..models import Account, Agreement, Period, JournalEntry, JournalLine, NotificationOutbox, IdempotencyRecord

ZERO=Decimal('0.00')
class DomainError(Exception): pass

def bd(value):
    try:
        amount=Decimal(str(value))
        if not amount.is_finite() or amount<=0 or amount!=amount.quantize(Decimal('0.01')): raise ValueError
        return amount
    except (InvalidOperation,ValueError): raise DomainError('Amount must be positive BDT with at most two decimals')

def total(qs,field='amount'):
    from django.db.models import Sum
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

CHART=[('CASH','Cash and bank','asset',''),('CUSTOMER_RECEIVABLE','Customer receivable','asset','customer'),('RENT_REVENUE','Rent revenue','revenue',''),('OTHER_REVENUE','Other revenue','revenue',''),
    ('OWNER_RENT_EXPENSE','Owner rent expense','expense',''),('OWNER_PAYABLE','Owner payable','liability','owner'),('DRIVER_SALARY_EXPENSE','Driver salary expense','expense',''),('DRIVER_PAYABLE','Driver payable','liability','driver'),
    ('OPERATING_EXPENSE','Operating expense','expense',''),('OWNER_RECOVERABLE','Owner recoverable','asset','owner'),('CUSTOMER_RECOVERABLE','Customer recoverable','asset','customer'),('OPENING_BALANCE','Opening balance equity','equity',''),('DRIVER_RECOVERABLE','Driver recoverable','asset','driver')]

def ensure_accounts(org):
    found={a.code:a for a in Account.objects.filter(organization=org)}
    for code,name,kind,sub in CHART:
        if code not in found:found[code]=Account.objects.create(organization=org,code=code,name=name,type=kind,subledger_kind=sub)
    return found

def journal(org,source_type,source_id,lines,period=None):
    """Post one balanced entry. A line is (account_code, debit, credit[, party[, period]]); lines without their own period use `period`.
    Each line carries party/agreement/period/vehicle dimensions so the ledger can be read per period and party."""
    debit=sum((Decimal(str(l[1])) for l in lines),ZERO)
    credit=sum((Decimal(str(l[2])) for l in lines),ZERO)
    if debit!=credit or debit<=0: raise DomainError('Journal is not balanced')
    accounts=ensure_accounts(org)
    entry=JournalEntry.objects.create(organization=org,source_type=source_type,source_id=str(source_id))
    rows=[]
    for l in lines:
        party=l[3] if len(l)>3 else None
        p=l[4] if len(l)>4 and l[4] is not None else period
        rows.append(JournalLine(entry=entry,account=l[0],account_ref=accounts[l[0]],debit=l[1],credit=l[2],party=party,period=p,agreement_id=p.agreement_id if p else None,vehicle_id=p.agreement.vehicle_id if p else None))
    JournalLine.objects.bulk_create(rows)
    return entry

def period_parties(period):
    """Customer, owner and driver parties the period was issued with (the obligations carry them, so history survives reassignment)."""
    by_kind={o.kind:o.party for o in period.obligations.select_related('party').filter(kind__in=['customer_rent','owner_rent','driver_salary'])}
    return {'customer':by_kind.get('customer_rent') or period.agreement.customer,'owner':by_kind['owner_rent'],'driver':by_kind['driver_salary']}

def notice(org,event_key,party,event_type,payload):
    NotificationOutbox.objects.get_or_create(organization=org,event_key=event_key,recipient=party,defaults={'event_type':event_type,'payload':payload})

def validate_agreement(agreement):
    org=agreement.organization_id
    if agreement.vehicle.organization_id!=org or agreement.vehicle.owner.organization_id!=org or agreement.customer.organization_id!=org or agreement.driver.organization_id!=org:raise DomainError('Agreement relationships must belong to the organization')
    if agreement.vehicle.owner.kind!='owner' or agreement.customer.kind!='customer' or agreement.driver.kind!='driver':raise DomainError('Party roles do not match agreement')
    if agreement.end_date and agreement.end_date<agreement.start_date:raise DomainError('End date precedes start date')
    clash=Agreement.objects.filter(vehicle_id=agreement.vehicle_id,active=True).exclude(pk=agreement.pk).filter(Q(end_date__isnull=True)|Q(end_date__gte=agreement.start_date))
    if agreement.end_date:clash=clash.filter(start_date__lte=agreement.end_date)
    if clash.exists():raise DomainError('Vehicle has an overlapping active agreement')
    if agreement.driver_provider not in('operator','owner','customer') or agreement.driver_paid_by not in('operator','owner','customer'):raise DomainError('The driver provider and the party paying the driver must each be operator, owner or customer')
    if any(x<=0 for x in [agreement.customer_rent,agreement.owner_rent]) or agreement.driver_salary<0 or (agreement.driver_paid_by=='operator' and agreement.driver_salary<=0):raise DomainError('Monthly terms must be positive')

def locked_period(org,period_id):
    period=Period.objects.select_for_update().select_related('agreement','agreement__vehicle','agreement__customer','agreement__driver','agreement__vehicle__owner').get(pk=period_id,organization=org)
    if period.closed_at: raise DomainError('Period is closed; use an authorized adjustment workflow')
    return period
