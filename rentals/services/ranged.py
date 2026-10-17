"""Exact date-range accounting (M13c): opening balance, movements and closing balance for any [from, to].

Every journal line is read through a window:
  * an accrual line (source `obligation`: rent, owner rent, salary) is spread evenly over the days its agreement runs inside that month,
    with cumulative rounding so that any split of a month adds back to the month exactly;
  * every other line counts when from <= JournalEntry.event_date <= to.
The unchanged section maths (statements._sections) then runs on each window, so closing = opening + movements holds by linearity and the
figures reconcile with the period statements by construction."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from ..models import JournalLine
from .helpers import DomainError, ZERO
from .ranges import resolve_range
from .statements import _sections, customer_bill_state, payable_bill_state

CENTS=Decimal('0.01')
def q(x):return Decimal(x).quantize(CENTS,rounding=ROUND_HALF_UP)
def month_start(d):return d.replace(day=1)
def month_end(d):return (d.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)

def accrual_part(amount,a0,a1,lo,hi):
    """The share of `amount`, accrued evenly over a0..a1 (inclusive), that falls in [lo, hi]; a None bound is open."""
    amount=Decimal(amount);n=(a1-a0).days+1
    if n<=0:return ZERO
    def through(d):
        elapsed=min(max((d-a0).days+1,0),n)
        return amount if elapsed>=n else q(amount*elapsed/n)
    top=amount if hi is None else through(hi)
    bottom=ZERO if lo is None else through(lo-timedelta(days=1))
    return q(top-bottom)

def active_days(period):
    """The days of the period's month that its agreement runs (a driver agreement likewise); the whole month when that cannot be told."""
    ms,me=month_start(period.month),month_end(period.month)
    src=period.agreement if period.agreement_id else period.driver_agreement
    if src is None:return ms,me
    a0=max(ms,src.start_date);a1=min(me,src.end_date or me)
    return (a0,a1) if a0<=a1 else (ms,me)

def window_rows(lines,period,lo,hi):
    """[(account, source_type, debit, credit)] of one period seen through the window [lo, hi]."""
    a0,a1=active_days(period);out=[]
    for account,source,debit,credit,when in lines:
        if source=='obligation':
            d=accrual_part(debit,a0,a1,lo,hi) if debit else ZERO;c=accrual_part(credit,a0,a1,lo,hi) if credit else ZERO
        else:
            when=when or month_start(period.month)
            if (lo is not None and when<lo) or (hi is not None and when>hi):continue
            d,c=debit,credit
        if d or c:out.append((account,source,d,c))
    return out

def _lines_by_period(periods):
    by={p.pk:[] for p in periods}
    for pid,a,s,d,c,w in JournalLine.objects.filter(period__in=periods).values_list('period_id','account','entry__source_type','debit','credit','entry__event_date'):by[pid].append((a,s,d,c,w))
    return by

def ranged_sections(periods,lo,hi):
    """{period_id: {'opening','movements','closing'}} statement sections per period with ONE journal query. opening is None when lo is None."""
    periods=list(periods);by=_lines_by_period(periods);out={}
    for p in periods:
        lines=by[p.pk]
        out[p.pk]={'opening':_sections(p,window_rows(lines,p,None,lo-timedelta(days=1))) if lo is not None else None,
            'movements':_sections(p,window_rows(lines,p,lo,hi)),'closing':_sections(p,window_rows(lines,p,None,hi))}
    return out

def check_range(lo,hi):
    if lo is None or hi is None:raise DomainError('give_both_a_from_and_a_to')
    return resolve_range(lo,hi)

SIDE_FIELDS={'customer':('billed','received','carried_forward','due'),'owner':('gross','offsets','net','paid','carried_forward','due'),'driver':('earned','advance','paid','carried_forward','due')}

def agreement_range_statement(agreement,lo,hi):
    """One agreement over [lo, hi]: per side the opening balance (up to lo - 1), the movements and the closing balance (up to hi)."""
    lo,hi=check_range(lo,hi)
    from ..models import Period
    periods=list(Period.objects.filter(agreement=agreement).select_related('agreement','driver_agreement').order_by('month'))
    if not periods:periods=[]
    by=_lines_by_period(periods)
    merged={'opening':[],'movements':[],'closing':[]}
    for p in periods:
        lines=by[p.pk]
        merged['opening']+=window_rows(lines,p,None,lo-timedelta(days=1));merged['movements']+=window_rows(lines,p,lo,hi);merged['closing']+=window_rows(lines,p,None,hi)
    meta=periods[-1] if periods else None
    if meta is None:raise DomainError('this_agreement_has_no_issued_month_yet')
    secs={k:_sections(meta,rows) for k,rows in merged.items()}
    out={'agreement_id':agreement.pk,'from':lo.isoformat(),'to':hi.isoformat()}
    for side in ('customer','owner','driver'):
        state=customer_bill_state if side=='customer' else payable_bill_state
        mv={k:v for k,v in secs['movements'][side].items() if k in SIDE_FIELDS[side]}
        out[side]={'opening':{'due':secs['opening'][side]['due']},'movements':mv,'closing':{'due':secs['closing'][side]['due'],'bill_state':state(secs['closing'][side]['due'])}}
    out['operator']=secs['movements']['operator']
    return out
