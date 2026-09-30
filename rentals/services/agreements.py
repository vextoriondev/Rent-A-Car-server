from django.db import transaction
from ..models import Agreement, Period, Obligation
from .helpers import ZERO, DomainError, validate_agreement, journal, notice

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
