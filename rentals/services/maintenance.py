"""Maintenance cases. Opening one is open to mechanics; approving needs finance; money still moves only through the expense engine,
which links an expense to a case (record_expense: maintenance_case_id)."""
from django.db import transaction
from django.utils import timezone
from ..i18n import word
from ..models import MaintenanceCase, Vehicle
from .audit import audit, snap
from .helpers import DomainError, bd

FIELDS=('vehicle_id','issue','vendor','quote','downtime_from','downtime_to','status')
FLOW={'requested':{'approved','cancelled'},'approved':{'in_progress','cancelled'},'in_progress':{'done','cancelled'},'done':set(),'cancelled':set()}

def create_case(org,user,vehicle_id,issue,vendor='',quote=None,downtime_from=None,downtime_to=None):
    if not issue.strip():raise DomainError('describe_the_problem')
    vehicle=Vehicle.objects.get(pk=vehicle_id,organization=org)
    _dates(downtime_from,downtime_to)
    case=MaintenanceCase.objects.create(organization=org,vehicle=vehicle,reported_by=user,issue=issue.strip(),vendor=vendor.strip(),quote=bd(quote) if quote is not None else None,downtime_from=downtime_from,downtime_to=downtime_to)
    case.refresh_from_db()
    audit(org,user,'maintenance.create','maintenance_case',case.pk,after=snap(case,FIELDS))
    return case

def _dates(start,end):
    if start and end and end<start:raise DomainError('downtime_ends_before_it_starts')

@transaction.atomic
def update_case(org,user,case_id,*,issue=None,vendor=None,quote=None,downtime_from=None,downtime_to=None,status=None):
    case=MaintenanceCase.objects.select_for_update().get(pk=case_id,organization=org)
    before=snap(case,FIELDS)
    if case.status in('done','cancelled'):raise DomainError('this_case_is_closed')
    if issue is not None:
        if not issue.strip():raise DomainError('describe_the_problem')
        case.issue=issue.strip()
    if vendor is not None:case.vendor=vendor.strip()
    if quote is not None:case.quote=bd(quote)
    if downtime_from is not None:case.downtime_from=downtime_from
    if downtime_to is not None:case.downtime_to=downtime_to
    _dates(case.downtime_from,case.downtime_to)
    if status is not None and status!=case.status:
        if status=='approved':raise DomainError('approval_is_a_separate_action')
        if status not in FLOW[case.status]:raise DomainError('a_status_case_cannot_become_status2', status=word('maint',case.status), status2=word('maint',status))
        case.status=status
    case.save();case.refresh_from_db()
    audit(org,user,'maintenance.update','maintenance_case',case.pk,before=before,after=snap(case,FIELDS))
    return case

@transaction.atomic
def approve_case(org,user,case_id):
    case=MaintenanceCase.objects.select_for_update().get(pk=case_id,organization=org)
    if case.status!='requested':raise DomainError('only_requested_cases_can_be_approved')
    before=snap(case,FIELDS)
    case.status='approved';case.approved_by=user;case.approved_at=timezone.now();case.save()
    audit(org,user,'maintenance.approve','maintenance_case',case.pk,before=before,after=snap(case,FIELDS))
    return case
