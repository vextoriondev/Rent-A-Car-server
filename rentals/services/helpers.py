"""Shared building blocks for the domain services: money parsing, idempotency replay and the balanced journal."""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import Q
from ..models import Agreement, Period, JournalEntry, JournalLine, NotificationOutbox, IdempotencyRecord

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
    clash=Agreement.objects.filter(vehicle_id=agreement.vehicle_id,active=True).exclude(pk=agreement.pk).filter(Q(end_date__isnull=True)|Q(end_date__gte=agreement.start_date))
    if agreement.end_date:clash=clash.filter(start_date__lte=agreement.end_date)
    if clash.exists():raise DomainError('Vehicle has an overlapping active agreement')
    if any(x<=0 for x in [agreement.customer_rent,agreement.owner_rent,agreement.driver_salary]):raise DomainError('Monthly terms must be positive')

def locked_period(org,period_id):
    period=Period.objects.select_for_update().select_related('agreement','agreement__vehicle','agreement__customer','agreement__driver','agreement__vehicle__owner').get(pk=period_id,organization=org)
    if period.closed_at: raise DomainError('Period is closed; use an authorized adjustment workflow')
    return period
