"""Effective-dated terms: agreement versions, vehicle ownership history, driver assignments, overlap rules.

A version/owner/driver change applies to periods starting on/after its effective date (rounded up to a 1st) and is
rejected once any period in that range has been issued: a current or past month changes only via an explicit adjustment."""
from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from ..i18n import translate
from ..models import Agreement, AgreementVersion, DriverAssignment, Party, Period, Vehicle, VehicleOwnership
from .audit import audit
from . import dues, rent_calc
from .helpers import DomainError, bd, validate_agreement

TERM_FIELDS=('customer_rent','owner_rent','driver_salary','customer_rent_basis','owner_rent_basis','driver_salary_basis','payment_schedule','fuel_responsible','repair_responsible','owner_offset_allowed','approval_threshold','billing_cadence','payout_cadence','driver_provider','driver_paid_by','clauses','overtime_rate','overtime_unit')
MONEY_FIELDS=('customer_rent','owner_rent','driver_salary','approval_threshold','overtime_rate')
MIRROR=('customer_rent','owner_rent','driver_salary','customer_rent_basis','owner_rent_basis','driver_salary_basis','payment_schedule','fuel_responsible','repair_responsible','owner_offset_allowed','driver_provider','driver_paid_by','clauses')
RESPONSIBLE=('owner','operator','customer','driver')
DRIVER_ROLES=('operator','owner','customer')
DRIVER_PROVIDERS=DRIVER_ROLES+('none',)

def first_of_month_on_or_after(d:date) -> date:
    return d if d.day==1 else (d.replace(day=1)+timedelta(days=32)).replace(day=1)

def ensure_baseline(agreement):
    """Idempotently create v1 / ownership / driver assignment for agreements saved without them (fixtures, legacy rows)."""
    if not agreement.versions.exists():
        AgreementVersion.objects.create(agreement=agreement,number=1,effective_from=agreement.start_date,customer_rent=agreement.customer_rent,owner_rent=agreement.owner_rent,driver_salary=agreement.driver_salary,customer_rent_basis=agreement.customer_rent_basis,owner_rent_basis=agreement.owner_rent_basis,driver_salary_basis=agreement.driver_salary_basis,payment_schedule=agreement.payment_schedule,fuel_responsible=agreement.fuel_responsible,repair_responsible=agreement.repair_responsible,owner_offset_allowed=agreement.owner_offset_allowed,driver_provider=agreement.driver_provider,driver_paid_by=agreement.driver_paid_by,clauses=agreement.clauses,reason='Initial terms')
    if not VehicleOwnership.objects.filter(vehicle=agreement.vehicle).exists():
        VehicleOwnership.objects.create(organization=agreement.organization,vehicle=agreement.vehicle,owner=agreement.vehicle.owner,effective_from=agreement.start_date)
    if agreement.driver_id and not agreement.driver_assignments.exists():
        DriverAssignment.objects.create(organization=agreement.organization,agreement=agreement,driver=agreement.driver,effective_from=agreement.start_date)

def version_for(agreement,month:date):
    """Terms for a month: the latest version effective by the end of that month, so an agreement that starts mid-month (its v1 is dated the start day) still has terms for its first month. Revisions always start on the 1st."""
    v=agreement.versions.exclude(status='draft').filter(effective_from__lt=first_of_month_on_or_after(month+timedelta(days=1))).order_by('-effective_from','-number').first()
    if not v:raise DomainError('no_active_agreement_terms_for_this_month')
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
    if v.driver_provider=='none':agreement.driver=None
    elif a:agreement.driver=a.driver
    agreement.save()
    owner=owner_on(agreement.vehicle,today)
    if agreement.vehicle.owner_id!=owner.pk:
        agreement.vehicle.owner=owner;agreement.vehicle.save(update_fields=['owner'])

def check_overlaps(agreement):
    """Non-blocking driver warnings. Same-vehicle overlap blocks in validate_agreement."""
    if not agreement.driver_id:return []
    end=agreement.end_date or date.max
    others=Agreement.objects.filter(organization_id=agreement.organization_id,driver_id=agreement.driver_id,active=True,start_date__lte=end).exclude(pk=agreement.pk).filter(Q(end_date__isnull=True)|Q(end_date__gte=agreement.start_date))
    return [translate('warning_driver_also_assigned',{'ref':f'AGR-{o.pk}'}) for o in others]

@transaction.atomic
def create_agreement(org,user,agreement,reason='Initial terms',draft=False):
    """Single creation path (API create, quick, future callers): lock vehicle, validate, save, write v1/ownership/assignment. Returns (agreement, warnings)."""
    agreement.status='draft' if draft else 'active';agreement.active=not draft
    Vehicle.objects.select_for_update().get(pk=agreement.vehicle_id)
    validate_agreement(agreement)
    agreement.save()
    ensure_baseline(agreement)
    agreement.versions.update(status='draft' if draft else 'active',created_by=user,reason=reason)
    audit(org,user,'agreement.create','agreement',agreement.pk,after={'status':agreement.status,'vehicle_id':agreement.vehicle_id,'customer_id':agreement.customer_id,'driver_id':agreement.driver_id,'customer_rent':agreement.customer_rent,'owner_rent':agreement.owner_rent,'driver_salary':agreement.driver_salary})
    return agreement,check_overlaps(agreement)

@transaction.atomic
def activate_agreement(org,user,agreement_id):
    agreement=Agreement.objects.select_for_update(of=('self',)).select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org)
    if agreement.status!='draft':raise DomainError('only_draft_agreements_can_be_activated')
    agreement.status='active';agreement.active=True
    validate_agreement(agreement)
    agreement.save();agreement.versions.filter(status='draft').update(status='active')
    audit(org,user,'agreement.activate','agreement',agreement.pk,before={'status':'draft'},after={'status':'active'})
    return agreement,check_overlaps(agreement)

def _reject_issued(target,start:date,vehicle=False):
    qs=Period.objects.filter(month__gte=start.replace(day=1),**({'agreement__vehicle':target} if vehicle else {'agreement':target}))
    if qs.exists():raise DomainError('periods_from_that_date_are_already_issued_2')

@transaction.atomic
def revise_agreement(org,user,agreement_id,effective_from:date,changes:dict,reason:str):
    if not reason.strip():raise DomainError('a_reason_is_required')
    agreement=Agreement.objects.select_for_update(of=('self',)).select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org)
    if agreement.status!='active':raise DomainError('only_active_agreements_can_be_revised')
    ensure_baseline(agreement)
    start=effective_from                                  # M12: a revision may start on any date; the month it falls in is computed in dated segments
    if start<agreement.start_date:raise DomainError('revision_starts_before_the_agreement_starts')
    if agreement.end_date and start>agreement.end_date:raise DomainError('revision_starts_after_the_agreement_ends')
    _reject_issued(agreement,start)
    unknown=set(changes)-set(TERM_FIELDS)
    if unknown:raise DomainError('unknown_terms_sorted', sorted=sorted(unknown))
    live=agreement.versions.exclude(status='draft')
    previous=live.filter(effective_from__lte=start).order_by('-effective_from','-number').first() or live.order_by('-number').first()
    values={f:getattr(previous,f) for f in TERM_FIELDS}
    for f,v in changes.items():
        if v is not None:values[f]=(Decimal('0.00') if f=='driver_salary' and Decimal(str(v))==0 else bd(v)) if f in MONEY_FIELDS else v
    values['payment_schedule']=dues.normalize_schedule(values.get('payment_schedule'))
    if values['fuel_responsible'] not in RESPONSIBLE or values['repair_responsible'] not in RESPONSIBLE:raise DomainError('unsupported_responsible_party')
    if values['driver_provider'] not in DRIVER_PROVIDERS or values['driver_paid_by'] not in DRIVER_ROLES:raise DomainError('the_driver_provider_must_be_operator_owner')
    if values['driver_provider']=='none':values['driver_salary']=Decimal('0.00')
    if values['driver_provider']!='none' and values['driver_paid_by']=='operator' and values['driver_salary']<=0:raise DomainError('a_driver_paid_by_the_operator_needs')
    if live.filter(effective_from=start).exists():raise DomainError('a_version_already_starts_on_that_date')
    number=(agreement.versions.order_by('-number').values_list('number',flat=True).first() or 0)+1
    live.filter(effective_from__lt=start).update(status='superseded')
    version=AgreementVersion.objects.create(agreement=agreement,number=number,effective_from=start,status='active',reason=reason.strip(),created_by=user,**values)
    from .expense_rules import copy_rules
    copy_rules(previous,version)
    rent_calc.validate_versions(agreement.versions.exclude(status='draft'),agreement.start_date,agreement.end_date)   # rolls the revision back when a basis is unknown or a fixed total has no end
    sync_current_columns(agreement)
    version.refresh_from_db()
    audit(org,user,'agreement.revise','agreement',agreement.pk,before={f:getattr(previous,f) for f in TERM_FIELDS},after={f:getattr(version,f) for f in TERM_FIELDS}|{'number':version.number,'effective_from':version.effective_from},reason=reason)
    return version

@transaction.atomic
def assign_driver(org,user,agreement_id,driver_id,effective_from:date,salary_override=None):
    agreement=Agreement.objects.select_for_update(of=('self',)).select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org)
    driver=Party.objects.get(pk=driver_id,organization=org,kind='driver')
    ensure_baseline(agreement)
    start=first_of_month_on_or_after(effective_from)
    _reject_issued(agreement,start)
    if agreement.driver_assignments.filter(effective_from__gte=start).exists():raise DomainError('a_later_driver_assignment_already_exists')
    salary=bd(salary_override) if salary_override is not None else None
    agreement.driver_assignments.filter(effective_to__isnull=True).update(effective_to=start-timedelta(days=1))
    a=DriverAssignment.objects.create(organization=org,agreement=agreement,driver=driver,effective_from=start,salary_override=salary)
    sync_current_columns(agreement)
    probe=Agreement(pk=agreement.pk,organization=org,driver=driver,start_date=start,end_date=agreement.end_date)
    audit(org,user,'agreement.assign_driver','agreement',agreement.pk,after={'driver_id':driver.pk,'effective_from':start,'salary_override':salary})
    return a,check_overlaps(probe)

@transaction.atomic
def transfer_vehicle_ownership(org,user,vehicle_id,owner_id,effective_from:date):
    vehicle=Vehicle.objects.select_for_update(of=('self',)).select_related('owner').get(pk=vehicle_id,organization=org)
    owner=Party.objects.get(pk=owner_id,organization=org,kind='owner')
    for a in Agreement.objects.filter(vehicle=vehicle).select_related('vehicle','organization'):ensure_baseline(a)
    start=first_of_month_on_or_after(effective_from)
    _reject_issued(vehicle,start,vehicle=True)
    if vehicle.ownerships.filter(effective_from__gte=start).exists():raise DomainError('a_later_ownership_record_already_exists')
    vehicle.ownerships.filter(effective_to__isnull=True).update(effective_to=start-timedelta(days=1))
    o=VehicleOwnership.objects.create(organization=org,vehicle=vehicle,owner=owner,effective_from=start)
    current=owner_on(vehicle,date.today())
    if current.pk!=vehicle.owner_id:vehicle.owner=current;vehicle.save(update_fields=['owner'])
    audit(org,user,'vehicle.transfer_ownership','vehicle',vehicle.pk,after={'owner_id':owner.pk,'effective_from':start})
    return o

@transaction.atomic
def update_agreement_dates(org,user,agreement_id,changes):
    """Extend, shorten or remove the end date. Refused when a month that the change would reprice is already issued."""
    agreement=Agreement.objects.select_for_update(of=('self',)).get(pk=agreement_id,organization=org)
    unknown=set(changes)-{'end_date'}
    if unknown:raise DomainError('unknown_fields_sorted', sorted=sorted(unknown))
    old,new=agreement.end_date,changes.get('end_date')
    if new and new<agreement.start_date:raise DomainError('the_end_date_cannot_be_before_the')
    ends=[d for d in (old,new) if d is not None]
    if ends and old!=new:
        lo=min(ends)
        affected=lo.replace(day=1) if lo!=rent_calc.month_end(lo) else (lo+timedelta(days=1)).replace(day=1)   # a month-end cut reprices nothing in its own month
        if Period.objects.filter(agreement=agreement,month__gte=affected).exists():raise DomainError('a_month_that_this_change_would_reprice')
    agreement.end_date=new
    rent_calc.validate_versions(agreement.versions.exclude(status='draft'),agreement.start_date,new)
    agreement.save(update_fields=['end_date'])
    audit(org,user,'agreement.dates','agreement',agreement.pk,before={'end_date':old},after={'end_date':new})
    return agreement
