from django.db import transaction
from ..models import Agreement, Period, Obligation
from .audit import audit
from .helpers import ZERO, DomainError, validate_agreement, journal, notice
from .terms import ensure_baseline, version_for, owner_on, assignment_on

@transaction.atomic
def issue_period(org,agreement_id,month,user=None):
    """Issue a month under the terms, owner and driver effective for that month; the version is recorded on the period."""
    agreement=Agreement.objects.select_for_update().select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org,active=True)
    if month.day!=1:raise DomainError('Month must be its first day')
    if month<agreement.start_date.replace(day=1) or (agreement.end_date and month>agreement.end_date.replace(day=1)):raise DomainError('Agreement is not effective this month')
    validate_agreement(agreement)
    existing=Period.objects.filter(organization=org,agreement=agreement,month=month).first()
    if existing:return existing
    ensure_baseline(agreement)
    version=version_for(agreement,month)
    owner=owner_on(agreement.vehicle,month)
    assignment=assignment_on(agreement,month)
    salary=(assignment.salary_override or version.driver_salary) if version.driver_paid_by=='operator' else ZERO  # owner/customer-paid drivers: the obligation row keeps the party, no payable is booked
    period=Period.objects.create(organization=org,agreement=agreement,month=month,agreement_version=version)
    for kind,party,amount,lines in [
        (Obligation.Kind.CUSTOMER,agreement.customer,version.customer_rent,[('CUSTOMER_RECEIVABLE',version.customer_rent,ZERO,agreement.customer),('RENT_REVENUE',ZERO,version.customer_rent)]),
        (Obligation.Kind.OWNER,owner,version.owner_rent,[('OWNER_RENT_EXPENSE',version.owner_rent,ZERO),('OWNER_PAYABLE',ZERO,version.owner_rent,owner)]),
        (Obligation.Kind.DRIVER,assignment.driver,salary,[('DRIVER_SALARY_EXPENSE',salary,ZERO),('DRIVER_PAYABLE',ZERO,salary,assignment.driver)])]:
        ob=Obligation.objects.create(period=period,kind=kind,party=party,amount=amount)
        if amount>0:journal(org,'obligation',ob.pk,lines,period=period)
    notice(org,f'period:{period.pk}:customer',agreement.customer,'rent_issued',{'period_id':period.pk})
    audit(org,user,'period.issue','period',period.pk,after={'agreement_id':agreement.pk,'month':month,'version':version.number})
    return period
