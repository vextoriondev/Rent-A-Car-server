from django.db import transaction
from ..models import Agreement, Period, Obligation
from .audit import audit
from .helpers import ZERO, DomainError, validate_agreement, journal, notice
from . import rent_calc
from .terms import ensure_baseline, version_for, owner_on, assignment_on

@transaction.atomic
def issue_period(org,agreement_id,month,user=None):
    """Issue a month under the terms, owner and driver effective for that month; the version is recorded on the period."""
    agreement=Agreement.objects.select_for_update(of=('self',)).select_related('vehicle__owner','customer','driver').get(pk=agreement_id,organization=org,active=True)
    if month.day!=1:raise DomainError('month_must_be_its_first_day')
    if month<agreement.start_date.replace(day=1) or (agreement.end_date and month>agreement.end_date.replace(day=1)):raise DomainError('agreement_is_not_effective_this_month')
    validate_agreement(agreement)
    existing=Period.objects.filter(organization=org,agreement=agreement,month=month).first()
    if existing:return existing
    ensure_baseline(agreement)
    version=version_for(agreement,month)
    owner=owner_on(agreement.vehicle,month)
    assignment=assignment_on(agreement,month) if version.driver_provider!='none' else None
    if version.driver_provider!='none' and not assignment:raise DomainError('assign_a_driver_before_issuing_this_month')
    live=list(agreement.versions.exclude(status='draft'))
    def side(name):
        field,basis=next((f,b) for n,f,b in rent_calc.SIDES if n==name)
        override=assignment.salary_override if name=='driver' and assignment else None
        zero=(lambda v:v.driver_paid_by!='operator') if name=='driver' else None   # owner/customer-paid drivers: the obligation row keeps the party, no payable is booked
        return rent_calc.month_amount(rent_calc.build_segments(rent_calc.versions_entries(live,field,basis,override,zero),agreement.start_date,agreement.end_date),month)
    customer_rent,owner_rent,salary=side('customer'),side('owner'),side('driver')
    period=Period.objects.create(organization=org,agreement=agreement,month=month,agreement_version=version)
    rows=[
        (Obligation.Kind.CUSTOMER,agreement.customer,customer_rent,[('CUSTOMER_RECEIVABLE',customer_rent,ZERO,agreement.customer),('RENT_REVENUE',ZERO,customer_rent)]),
        (Obligation.Kind.OWNER,owner,owner_rent,[('OWNER_RENT_EXPENSE',owner_rent,ZERO),('OWNER_PAYABLE',ZERO,owner_rent,owner)]),
        ]
    if assignment:rows.append((Obligation.Kind.DRIVER,assignment.driver,salary,[('DRIVER_SALARY_EXPENSE',salary,ZERO),('DRIVER_PAYABLE',ZERO,salary,assignment.driver)]))
    for kind,party,amount,lines in rows:
        ob=Obligation.objects.create(period=period,kind=kind,party=party,amount=amount)
        if amount>0:journal(org,'obligation',ob.pk,lines,period=period,event_date=month)
    notice(org,f'period:{period.pk}:customer',agreement.customer,'rent_issued',{'period_id':period.pk})
    audit(org,user,'period.issue','period',period.pk,after={'agreement_id':agreement.pk,'month':month,'version':version.number})
    return period
