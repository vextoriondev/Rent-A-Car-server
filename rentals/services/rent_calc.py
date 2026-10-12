"""Date-based rent (M12). Pure functions: no database, no clock.

A side (customer rent, owner rent or driver salary) has dated versions. Each version covers the range
[max(effective_from, agreement start), next version's effective_from - 1 or the agreement end] and prices it by a basis:
  per_day      days x rate
  per_month    the rate exactly for a whole calendar month, otherwise rate x days / days in that month
  fixed_total  the total spread over the version's own days; every month takes days x the rounded daily rate except the month
               holding the version's last day, which takes the rest, so the months always add up to exactly the total.
All money is rounded half up to 0.01. The end date is inclusive."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from .helpers import DomainError

BASES=('per_day','per_month','fixed_total')
BASIS_LABEL={'per_day':'per day','per_month':'per month','fixed_total':'fixed total'}
CENTS=Decimal('0.01')
ZERO=Decimal('0.00')

def q(x):return Decimal(x).quantize(CENTS,rounding=ROUND_HALF_UP)
def days_in_month(d):return monthrange(d.year,d.month)[1]
def month_start(d):return d.replace(day=1)
def month_end(d):return d.replace(day=days_in_month(d))
def days(a,b):return (b-a).days+1

@dataclass(frozen=True)
class Seg:
    start:date
    end:date|None          # None = open-ended
    basis:str
    amount:Decimal         # the rate per day, the monthly amount, or the fixed total

def build_segments(entries,agreement_start,agreement_end=None):
    """entries: [(effective_from, basis, amount)] for one side. Returns the dated segments, validating each basis."""
    entries=sorted(entries,key=lambda e:e[0])
    out=[]
    for i,(eff,basis,amount) in enumerate(entries):
        if basis not in BASES:raise DomainError(f'Unknown rent basis "{basis}"; use per day, per month or fixed total')
        start=max(eff,agreement_start)
        later=entries[i+1][0]-timedelta(days=1) if i+1<len(entries) else None
        ends=[x for x in (later,agreement_end) if x is not None]
        end=min(ends) if ends else None
        if end is not None and end<start:continue            # the version never applies inside the agreement
        if basis=='fixed_total' and end is None:raise DomainError('A fixed total needs an end date (on the agreement, or by a later revision)')
        out.append(Seg(start,end,basis,Decimal(amount)))
    return out

def window_amount(seg,ws,we):
    """Amount of one segment for the days ws..we (inside one calendar month and inside the segment)."""
    n=days(ws,we)
    if seg.basis=='per_day':return q(seg.amount*n)
    if seg.basis=='per_month':
        if ws==month_start(ws) and we==month_end(ws):return q(seg.amount)
        return q(seg.amount*n/days_in_month(ws))
    rate=q(seg.amount/days(seg.start,seg.end))
    if we==seg.end:return q(seg.amount-rate*(ws-seg.start).days)    # the last window takes what is left of the total
    return q(rate*n)

def month_amount(segments,month):
    """What the segments bill for the calendar month starting on `month`."""
    ms,me=month_start(month),month_end(month)
    total=ZERO
    for s in segments:
        ws=max(s.start,ms);we=min(s.end or me,me)
        if ws<=we:total+=window_amount(s,ws,we)
    return q(total)

def versions_entries(versions,field,basis_field,override=None,zero_if=None):
    """[(effective_from, basis, amount)] from agreement versions. `override` replaces every amount (a driver's own salary);
    `zero_if(version)` zeroes versions where nothing is payable (a driver paid by someone else)."""
    out=[]
    for v in versions:
        amount=Decimal(override) if override is not None else Decimal(getattr(v,field))
        if zero_if and zero_if(v):amount=ZERO
        out.append((v.effective_from,getattr(v,basis_field),amount))
    return out

SIDES=(('customer','customer_rent','customer_rent_basis'),('owner','owner_rent','owner_rent_basis'),('driver','driver_salary','driver_salary_basis'))

def validate_versions(versions,agreement_start,agreement_end):
    """Raises DomainError when any side's segments are invalid (unknown basis, a fixed total without an end)."""
    for _,field,basis in SIDES:build_segments(versions_entries(versions,field,basis),agreement_start,agreement_end)
