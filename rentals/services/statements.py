from ..models import Obligation, Allocation, OwnerOffset, Expense
from .helpers import total

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
