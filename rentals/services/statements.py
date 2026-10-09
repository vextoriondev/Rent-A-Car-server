"""Period statements.

Source of truth (decided in M4): the **journal**. `statement()` is the projection of `ledger_statement()`, which reads
one query of journal lines by period dimension. `obligation_statement()` is the independent obligations/allocations
view; it is kept only as the cross-check that `check_ledger` and the reconciliation tests assert against."""
from decimal import Decimal
from ..models import Allocation, Expense, JournalLine, Obligation, OwnerOffset, Payment
from .helpers import ZERO, total

BILL={'obligation','charge'}
PAY={'payment','payment_advance'}
MARGIN_KINDS={'customer_rent','owner_rent','driver_salary','charge','credit','adjustment','overtime','absence_deduction','fine'}

def customer_bill_state(due):
    """paid = nothing left to collect (an overpayment also counts as paid); owed_to_us = the customer still owes the operator (M13b, D4)."""
    return 'owed_to_us' if due>0 else 'paid'

def payable_bill_state(due):
    """Owner or driver side: outstanding = we owe them; owed_to_us = they owe the operator (negative due); paid = zero."""
    return 'outstanding' if due>0 else 'owed_to_us' if due<0 else 'paid'

def _sections(period,rows):
    """Statement figures from one period's journal rows [(account, source_type, debit, credit)]."""
    def nd(account,sources=None):return sum((d-c for a,s,d,c in rows if a==account and (sources is None or s in sources)),ZERO)
    def nc(account,sources=None):return -nd(account,sources)
    c_billed=nd('CUSTOMER_RECEIVABLE',BILL);c_carried=nc('CUSTOMER_RECEIVABLE',{'carry_out'});c_received=nc('CUSTOMER_RECEIVABLE',{'payment'})
    o_gross=nc('OWNER_PAYABLE',BILL);o_offsets=nd('OWNER_PAYABLE',{'expense','expense_reversal'});o_paid=nd('OWNER_PAYABLE',PAY);o_carried=nd('OWNER_PAYABLE',{'carry_out'})
    d_earned=nc('DRIVER_PAYABLE',BILL);d_paid=nd('DRIVER_PAYABLE',PAY);d_advance=nd('DRIVER_PAYABLE',{'payment_advance'});d_carried=nd('DRIVER_PAYABLE',{'carry_out'})
    owner_due=o_gross-o_offsets-o_carried-o_paid
    return {'period_id':period.pk,'month':period.month.isoformat(),'closed':bool(period.closed_at),
        'customer':{'billed':c_billed,'received':c_received,'carried_forward':c_carried,'due':c_billed-c_carried-c_received,'bill_state':customer_bill_state(c_billed-c_carried-c_received),'separate_recoverable':nd('CUSTOMER_RECOVERABLE')},
        'owner':{'gross':o_gross,'offsets':o_offsets,'net':o_gross-o_offsets,'paid':o_paid,'carried_forward':o_carried,'due':owner_due,'separate_recoverable':nd('OWNER_RECOVERABLE'),'owner_owes_operator':owner_due<0,'bill_state':payable_bill_state(owner_due)},
        'driver':{'earned':d_earned,'paid':d_paid,'advance':d_advance,'carried_forward':d_carried,'due':d_earned-d_carried-d_paid,'bill_state':payable_bill_state(d_earned-d_carried-d_paid),'separate_recoverable':nd('DRIVER_RECOVERABLE')},
        'operator':{'accrual_margin':nc('RENT_REVENUE')+nc('OTHER_REVENUE')-nd('OWNER_RENT_EXPENSE')-nd('DRIVER_SALARY_EXPENSE')-nd('OPERATING_EXPENSE'),'recorded_cash_movement':nd('CASH'),'operator_borne_costs':nd('OPERATING_EXPENSE')}}

def ledger_statement(period):
    return _sections(period,list(JournalLine.objects.filter(period=period).values_list('account','entry__source_type','debit','credit')))

def bulk_ledger_statements(periods):
    """Statements for many periods with ONE journal query (reports must not run a query per period)."""
    periods=list(periods);grouped={p.pk:[] for p in periods}
    for pid,a,s,d,c in JournalLine.objects.filter(period__in=periods).values_list('period_id','account','entry__source_type','debit','credit'):grouped[pid].append((a,s,d,c))
    return {p.pk:_sections(p,grouped[p.pk]) for p in periods}

def obligation_statement(period):
    """Same figures from obligations, allocations and expenses only (no journal)."""
    obs=list(period.obligations.select_related('party'))
    def side(kind):return [o for o in obs if o.party.kind==kind]
    def amount(items,keep=lambda o:True):return sum((o.amount for o in items if keep(o)),ZERO)
    def received(items):return total(Allocation.objects.filter(obligation__in=items))
    cust,own,drv=side('customer'),side('owner'),side('driver')
    billed=lambda items:amount(items,lambda o:o.kind!='carry_out')
    carried=lambda items:-amount(items,lambda o:o.kind=='carry_out')
    margin=lambda items:amount(items,lambda o:o.kind in MARGIN_KINDS)
    posted=Expense.objects.filter(period=period,state=Expense.State.POSTED)
    operator_cost=total(posted.filter(responsible='operator'))  # the operator's own cost, whoever fronted the money
    operator_paid=total(posted.filter(paid_by='operator'))
    offsets=total(OwnerOffset.objects.filter(obligation__in=own,expense__state=Expense.State.POSTED))
    owner_recoverable=total(posted.filter(responsible='owner',offset_owner=False).exclude(paid_by='owner'))+amount(own,lambda o:o.kind=='reimbursement')
    customer_recoverable=total(posted.filter(responsible='customer').exclude(paid_by='customer'))-amount(cust,lambda o:o.kind=='reimbursement')
    driver_recoverable=total(posted.filter(responsible='driver').exclude(paid_by='driver'))+amount(drv,lambda o:o.kind=='reimbursement')
    c_recv,o_paid,d_paid=received(cust),received(own),received(drv)
    owner_due=billed(own)-offsets-carried(own)-o_paid
    return {'period_id':period.pk,'month':period.month.isoformat(),'closed':bool(period.closed_at),
        'customer':{'billed':billed(cust),'received':c_recv,'carried_forward':carried(cust),'due':billed(cust)-carried(cust)-c_recv,'separate_recoverable':customer_recoverable},
        'owner':{'gross':billed(own),'offsets':offsets,'net':billed(own)-offsets,'paid':o_paid,'carried_forward':carried(own),'due':owner_due,'separate_recoverable':owner_recoverable,'owner_owes_operator':owner_due<0},
        'driver':{'earned':billed(drv),'paid':d_paid,'advance':total(Allocation.objects.filter(obligation__in=drv,payment__kind=Payment.Kind.ADVANCE)),'carried_forward':carried(drv),'due':billed(drv)-carried(drv)-d_paid,'separate_recoverable':driver_recoverable},
        'operator':{'accrual_margin':margin(cust)-margin(own)-margin(drv)-operator_cost,'recorded_cash_movement':c_recv-o_paid-d_paid-operator_paid,'operator_borne_costs':operator_cost}}

def statement(period):
    return ledger_statement(period)

def diff_statements(a,b):
    """Paths where two statements disagree, e.g. ['owner.due']; empty means they reconcile."""
    out=[]
    for section in ('customer','owner','driver','operator'):
        for key in a[section]:
            if key=='bill_state':continue  # derived from 'due', only present on the ledger statement
            if a[section][key]!=b[section].get(key):out.append(f'{section}.{key}')
    return out
