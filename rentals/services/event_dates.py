"""Backfill of JournalEntry.event_date (M13c) for entries posted before the column existed. Used by migration 0025 and by a test.

payment / payment_advance -> the payment's event_date; expense -> the expense's event_date; expense_reversal -> the day it was reversed (else the
event_date); charge -> the day it was posted clamped into its period's month; carry_out -> the first day of the month after its period;
obligation (rent, owner rent, salary accrual) -> the first day of its period's month; anything else -> the day it was posted."""
from datetime import date, timedelta

def _month_start(d):return d.replace(day=1)
def _month_end(d):return (d.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)

def backfill_event_dates(models=None):
    if models is None:
        from ..models import Charge, Expense, JournalEntry, JournalLine, Payment
    else:Charge,Expense,JournalEntry,JournalLine,Payment=(models[k] for k in ('Charge','Expense','JournalEntry','JournalLine','Payment'))
    todo=list(JournalEntry.objects.filter(event_date__isnull=True))
    if not todo:return 0
    month_of={}
    for entry_id,month in JournalLine.objects.filter(entry__in=[e.pk for e in todo],period__isnull=False).values_list('entry_id','period__month'):month_of.setdefault(entry_id,month)
    payments=dict(Payment.objects.values_list('pk','event_date'))
    expenses={pk:(ev,rev) for pk,ev,rev in Expense.objects.values_list('pk','event_date','reversed_at')}
    charges={pk:(created,month) for pk,created,month in Charge.objects.values_list('pk','created_at','period__month')}
    for e in todo:
        st=e.source_type;sid=int(e.source_id) if str(e.source_id).isdigit() else None
        when=None
        if st in('payment','payment_advance'):when=payments.get(sid)
        elif st=='expense' and sid in expenses:when=expenses[sid][0]
        elif st=='expense_reversal' and sid in expenses:ev,rev=expenses[sid];when=rev.date() if rev else ev
        elif st=='charge' and sid in charges:
            created,month=charges[sid];day=created.date();ms,me=_month_start(month),_month_end(month);when=min(max(day,ms),me)
        elif st=='carry_out' and sid in charges:when=_month_end(charges[sid][1])+timedelta(days=1)
        elif st=='obligation':when=month_of.get(e.pk)
        e.event_date=when or e.posted_at.date()
    JournalEntry.objects.bulk_update(todo,['event_date'])
    return len(todo)
