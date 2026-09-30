from django.db import transaction
from django.utils import timezone
from ..models import Period, Expense
from .helpers import DomainError, notice
from .statements import statement

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
