"""Effective-dated terms: agreement versions, vehicle ownership history, driver assignments, overlap rules.

A version/owner/driver change applies to periods starting on/after its effective date (rounded up to a 1st) and is
rejected once any period in that range has been issued: a current or past month changes only via an explicit adjustment."""
from datetime import date, timedelta
from django.db import transaction
from django.db.models import Q
from ..models import Agreement, AgreementVersion, DriverAssignment, Party, Period, Vehicle, VehicleOwnership
from .helpers import DomainError, bd, validate_agreement

TERM_FIELDS=('customer_rent','owner_rent','driver_salary','fuel_responsible','repair_responsible','owner_offset_allowed','approval_threshold','billing_cadence','payout_cadence')
MONEY_FIELDS=('customer_rent','owner_rent','driver_salary','approval_threshold')
MIRROR=('customer_rent','owner_rent','driver_salary','fuel_responsible','repair_responsible','owner_offset_allowed')

def first_of_month_on_or_after(d:date) -> date:
    return d if d.day==1 else (d.replace(day=1)+timedelta(days=32)).replace(day=1)

def ensure_baseline(agreement):
    """Idempotently create v1 / ownership / driver assignment for agreements saved without them (fixtures, legacy rows)."""
    if not agreement.versions.exists():
        AgreementVersion.objects.create(agreement=agreement,number=1,effective_from=agreement.start_date,customer_rent=agreement.customer_rent,owner_rent=agreement.owner_rent,driver_salary=agreement.driver_salary,fuel_responsible=agreement.fuel_responsible,repair_responsible=agreement.repair_responsible,owner_offset_allowed=agreement.owner_offset_allowed,reason='Initial terms')
    if not VehicleOwnership.objects.filter(vehicle=agreement.vehicle).exists():
        VehicleOwnership.objects.create(organization=agreement.organization,vehicle=agreement.vehicle,owner=agreement.vehicle.owner,effective_from=agreement.start_date)
    if not agreement.driver_assignments.exists():
        DriverAssignment.objects.create(organization=agreement.organization,agreement=agreement,driver=agreement.driver,effective_from=agreement.start_date)

def version_for(agreement,month:date):
    v=agreement.versions.exclude(status='draft').filter(effective_from__lte=month).order_by('-effective_from','-number').first()
    if not v:raise DomainError('No active agreement terms for this month')
    return v

def owner_on(vehicle,day:date):
    o=vehicle.ownerships.filter(effective_from__lte=day).filter(Q(effective_to__isnull=True)|Q(effective_to__gte=day)).order_by('-effective_from').first()
    return o.owner if o else vehicle.owner

def assignment_on(agreement,day:date):
    a=agreement.driver_assignments.filter(effective_from__lte=day).filter(Q(effective_to__isnull=True)|Q(effective_to__gte=day)).order_by('-effective_from').first()
    return a or agreement.driver_assignments.order_by('effective_from').first()

def sync_current_columns(agreement,today=None):
    """Keep the legacy Agreement/Vehicle columns equal to what is effective today so existing readers (snapshot, agreement_dict) keep their shape."""
    today=today or date.today()
    live=agreement.versions.exclude(status='draft')
    v=live.filter(effective_from__lte=today).order_by('-effective_from','-number').first() or live.order_by('effective_from').first()
    for f in MIRROR:setattr(agreement,f,getattr(v,f))
    a=assignment_on(agreement,today)
    if a:agreement.driver=a.driver
    agreement.save()
    owner=owner_on(agreement.vehicle,today)
    if agreement.vehicle.owner_id!=owner.pk:
        agreement.vehicle.owner=owner;agreement.vehicle.save(update_fields=['owner'])

def check_overlaps(agreement):
    """Non-blocking driver warnings. Same-vehicle overlap blocks in validate_agreement."""
    end=agreement.end_date or date.max
    others=Agreement.objects.filter(organization_id=agreement.organization_id,driver_id=agreement.driver_id,active=True,start_date__lte=end).exclude(pk=agreement.pk).filter(Q(end_date__isnull=True)|Q(end_date__gte=agreement.start_date))
    return [f'Driver is also assigned to agreement AGR-{o.pk} during this period' for o in others]

@transaction.atomic
def create_agreement(org,user,agreement,reason='Initial terms',draft=False):
    """Single creation path (API create, quick, future callers): lock vehicle, validate, save, write v1/ownership/assignment. Returns (agreement, warnings)."""
    agreement.status='draft' if draft else 'active';agreement.active=not draft
    Vehicle.objects.select_for_update().get(pk=agreement.vehicle_id)
    validate_agreement(agreement)
    agreement.save()
    ensure_baseline(agreement)
    agreement.versions.update(status='draft' if draft else 'active',created_by=user,reason=reason)
    return agreement,check_overlaps(agreement)

@transaction.atomic
def activate_agreement(org,user,agreement_id):
    agreement=Agreement.objects.select_for_update().select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org)
    if agreement.status!='draft':raise DomainError('Only draft agreements can be activated')
    agreement.status='active';agreement.active=True
    validate_agreement(agreement)
    agreement.save();agreement.versions.filter(status='draft').update(status='active')
    return agreement,check_overlaps(agreement)

def _reject_issued(target,start:date,vehicle=False):
    qs=Period.objects.filter(month__gte=start,**({'agreement__vehicle':target} if vehicle else {'agreement':target}))
    if qs.exists():raise DomainError('Periods from that date are already issued or closed; use an explicit adjustment instead of a retroactive change')

@transaction.atomic
def revise_agreement(org,user,agreement_id,effective_from:date,changes:dict,reason:str):
    if not reason.strip():raise DomainError('A reason is required')
    agreement=Agreement.objects.select_for_update().select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org)
    if agreement.status!='active':raise DomainError('Only active agreements can be revised')
    ensure_baseline(agreement)
    start=first_of_month_on_or_after(effective_from)
    if agreement.end_date and start>agreement.end_date:raise DomainError('Revision starts after the agreement ends')
    _reject_issued(agreement,start)
    unknown=set(changes)-set(TERM_FIELDS)
    if unknown:raise DomainError(f'Unknown terms: {sorted(unknown)}')
    live=agreement.versions.exclude(status='draft')
    previous=live.filter(effective_from__lte=start).order_by('-effective_from','-number').first() or live.order_by('-number').first()
    values={f:getattr(previous,f) for f in TERM_FIELDS}
    for f,v in changes.items():
        if v is not None:values[f]=bd(v) if f in MONEY_FIELDS else v
    if values['fuel_responsible'] not in('owner','operator','customer') or values['repair_responsible'] not in('owner','operator','customer'):raise DomainError('Unsupported responsible party')
    if live.filter(effective_from=start).exists():raise DomainError('A version already starts on that date')
    number=(agreement.versions.order_by('-number').values_list('number',flat=True).first() or 0)+1
    live.filter(effective_from__lt=start).update(status='superseded')
    version=AgreementVersion.objects.create(agreement=agreement,number=number,effective_from=start,status='active',reason=reason.strip(),created_by=user,**values)
    from .expense_rules import copy_rules
    copy_rules(previous,version)
    sync_current_columns(agreement)
    version.refresh_from_db()
    return version

@transaction.atomic
def assign_driver(org,user,agreement_id,driver_id,effective_from:date,salary_override=None):
    agreement=Agreement.objects.select_for_update().select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org)
    driver=Party.objects.get(pk=driver_id,organization=org,kind='driver')
    ensure_baseline(agreement)
    start=first_of_month_on_or_after(effective_from)
    _reject_issued(agreement,start)
    if agreement.driver_assignments.filter(effective_from__gte=start).exists():raise DomainError('A later driver assignment already exists')
    salary=bd(salary_override) if salary_override is not None else None
    agreement.driver_assignments.filter(effective_to__isnull=True).update(effective_to=start-timedelta(days=1))
    a=DriverAssignment.objects.create(organization=org,agreement=agreement,driver=driver,effective_from=start,salary_override=salary)
    sync_current_columns(agreement)
    probe=Agreement(pk=agreement.pk,organization=org,driver=driver,start_date=start,end_date=agreement.end_date)
    return a,check_overlaps(probe)

@transaction.atomic
def transfer_vehicle_ownership(org,user,vehicle_id,owner_id,effective_from:date):
    vehicle=Vehicle.objects.select_for_update().select_related('owner').get(pk=vehicle_id,organization=org)
    owner=Party.objects.get(pk=owner_id,organization=org,kind='owner')
    for a in Agreement.objects.filter(vehicle=vehicle).select_related('vehicle','organization'):ensure_baseline(a)
    start=first_of_month_on_or_after(effective_from)
    _reject_issued(vehicle,start,vehicle=True)
    if vehicle.ownerships.filter(effective_from__gte=start).exists():raise DomainError('A later ownership record already exists')
    vehicle.ownerships.filter(effective_to__isnull=True).update(effective_to=start-timedelta(days=1))
    o=VehicleOwnership.objects.create(organization=org,vehicle=vehicle,owner=owner,effective_from=start)
    current=owner_on(vehicle,date.today())
    if current.pk!=vehicle.owner_id:vehicle.owner=current;vehicle.save(update_fields=['owner'])
    return o
