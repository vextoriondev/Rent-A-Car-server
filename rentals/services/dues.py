"""Payment schedule and dues (M12 S2).

Each side of an agreement (customer pays us, we pay the owner, we pay the driver) has a payment schedule on each version:
  {"kind":"weekly","weekday":0-6 (Monday=0)} | {"kind":"monthly","day":1-31 or "last"} | {"kind":"dates","dates":["YYYY-MM-DD",...]}
Unset means monthly on the last day, which is what month-end billing always did.

Dues are computed on demand and never stored, so they cannot go stale after a revision. A due covers the days since the previous due date
(the first from the agreement start, the last up to the agreement end, which closes the final stretch with its own due) and its amount is
the cumulative accrual difference cum(covers_to) - cum(covers_from - 1), so every side's dues add up to exactly what the months bill.
Money paid on that side's rent or salary obligations is applied to the dues oldest first."""
from datetime import date, timedelta
from django.db.models import Sum
from ..models import Allocation
from . import rent_calc
from .helpers import ZERO, DomainError

SIDES=('customer','owner','driver')
DEFAULT_SPEC={'kind':'monthly','day':'last'}
KIND={'customer':'customer_rent','owner':'owner_rent','driver':'driver_salary'}
HORIZON_DAYS=62

def normalize_schedule(schedule):
    """Validated copy of a schedule ({} when unset). Raises DomainError for anything else."""
    if not schedule:return {}
    if not isinstance(schedule,dict):raise DomainError('The payment schedule must list customer, owner and driver')
    out={}
    for side,spec in schedule.items():
        if side not in SIDES:raise DomainError(f'Unknown side "{side}" in the payment schedule; use customer, owner or driver')
        if not isinstance(spec,dict):raise DomainError('Each payment schedule needs a kind: weekly, monthly or dates')
        kind=spec.get('kind')
        if kind=='weekly':
            wd=spec.get('weekday')
            if not isinstance(wd,int) or isinstance(wd,bool) or not 0<=wd<=6:raise DomainError('A weekly schedule needs a weekday from 0 (Monday) to 6 (Sunday)')
            out[side]={'kind':'weekly','weekday':wd}
        elif kind=='monthly':
            day=spec.get('day')
            if day!='last' and (not isinstance(day,int) or isinstance(day,bool) or not 1<=day<=31):raise DomainError('A monthly schedule needs a day from 1 to 31, or "last"')
            out[side]={'kind':'monthly','day':day}
        elif kind=='dates':
            raw=spec.get('dates')
            if not isinstance(raw,list) or not raw:raise DomainError('A schedule of chosen dates needs at least one date')
            try:dates=sorted({date.fromisoformat(str(x)) for x in raw})
            except ValueError as exc:raise DomainError('Chosen dates must be written as YYYY-MM-DD') from exc
            out[side]={'kind':'dates','dates':[d.isoformat() for d in dates]}
        else:raise DomainError('A payment schedule kind must be weekly, monthly or dates')
    return out

def spec_for(version,side):return (version.payment_schedule or {}).get(side) or DEFAULT_SPEC
def is_default(versions,side):return all(spec_for(v,side)==DEFAULT_SPEC for v in versions)

def _spec_dates(spec,lo,hi):
    if spec['kind']=='dates':return [d for d in (date.fromisoformat(x) for x in spec['dates']) if lo<=d<=hi]
    out=[]
    if spec['kind']=='weekly':
        d=lo+timedelta(days=(spec['weekday']-lo.weekday())%7)
        while d<=hi:out.append(d);d+=timedelta(days=7)
        return out
    m=rent_calc.month_start(lo)
    while m<=hi:
        last=rent_calc.days_in_month(m)
        d=m.replace(day=last if spec['day']=='last' else min(spec['day'],last))
        if lo<=d<=hi:out.append(d)
        m=(m+timedelta(days=32)).replace(day=1)
    return out

def accrued_through(segments,start,end,d):
    """Everything the segments bill from the agreement start through day d (inclusive)."""
    if d<start:return ZERO
    stop=min(d,end) if end else d
    total=ZERO;m=rent_calc.month_start(start)
    while m<rent_calc.month_start(stop):
        total+=rent_calc.month_amount(segments,m);m=(m+timedelta(days=32)).replace(day=1)
    full=rent_calc.month_amount(segments,m)
    a0=max(m,start);a1=rent_calc.month_end(m)
    if end:a1=min(a1,end)
    elapsed=rent_calc.days(a0,stop);active=rent_calc.days(a0,a1)
    return rent_calc.q(total+(full if elapsed>=active else rent_calc.q(full*elapsed/active)))

def _versions(agreement):return list(agreement.versions.exclude(status='draft').order_by('effective_from','number'))

def due_dates(versions,start,end,side,horizon):
    hi=min(end,horizon) if end else horizon
    found=set()
    for i,v in enumerate(versions):
        lo=max(v.effective_from,start)
        nxt=versions[i+1].effective_from-timedelta(days=1) if i+1<len(versions) else None
        top=min(x for x in (nxt,hi) if x is not None)
        if lo<=top:found.update(_spec_dates(spec_for(v,side),lo,top))
    if end and end<=horizon:found.add(end)               # the last stretch up to the end of the agreement closes with its own due
    return sorted(found)

def dues_for(agreement,side,today=None,until=None,versions=None):
    """[{side, due_date, covers_from, covers_to, amount, paid, state}] oldest first. state: paid | partly_paid | due | overdue."""
    if side not in SIDES:raise DomainError('Side must be customer, owner or driver')
    today=today or date.today()
    versions=versions if versions is not None else _versions(agreement)
    start,end=agreement.start_date,agreement.end_date
    horizon=until or (end if end else today+timedelta(days=HORIZON_DAYS))   # a dated agreement lists every due; an open-ended one only the next stretch
    field,basis=next((f,b) for n,f,b in rent_calc.SIDES if n==side)
    zero=(lambda v:v.driver_paid_by!='operator' or v.driver_provider=='none') if side=='driver' else None
    segments=rent_calc.build_segments(rent_calc.versions_entries(versions,field,basis,None,zero),start,end)
    dates=due_dates(versions,start,end,side,horizon)
    paid=(Allocation.objects.filter(obligation__period__agreement=agreement,obligation__kind=KIND[side]).aggregate(t=Sum('amount'))['t'] or ZERO) if getattr(agreement,'pk',None) else ZERO   # a preview has nothing paid
    out=[];prev=None
    for d in dates:
        frm=start if prev is None else prev+timedelta(days=1)
        amount=rent_calc.q(accrued_through(segments,start,end,d)-(accrued_through(segments,start,end,frm-timedelta(days=1)) if prev is not None else ZERO))
        applied=min(amount,paid);paid-=applied
        state='paid' if applied>=amount else 'overdue' if d<today else 'partly_paid' if applied>0 else 'due'
        out.append({'side':side,'due_date':d,'covers_from':frm,'covers_to':d,'amount':amount,'paid':rent_calc.q(applied),'state':state})
        prev=d
    return out

def next_due(items):
    """The oldest due that is not fully paid (it may already be overdue)."""
    return next((x for x in items if x['state']!='paid'),None)

def oldest_unpaid_due_date(agreement,side,versions,as_of):
    """Aging: the due date days overdue count from. None when everything up to as_of is paid."""
    items=[x for x in dues_for(agreement,side,today=as_of,until=as_of,versions=versions) if x['state']!='paid' and x['due_date']<=as_of]
    return items[0]['due_date'] if items else None

def preview_terms(start,end,amounts,bases,schedule,has_driver=True,months=3,dues_each=4):
    """What a set of terms would bill and when it would fall due, without saving anything (the wizard's summary line).
    amounts/bases: {'customer','owner','driver'} -> value."""
    from types import SimpleNamespace
    if end and end<start:raise DomainError('The end date is before the start date')
    schedule=normalize_schedule(schedule)
    v=SimpleNamespace(effective_from=start,customer_rent=amounts['customer'],owner_rent=amounts['owner'],driver_salary=amounts['driver'] if has_driver else ZERO,
        customer_rent_basis=bases['customer'],owner_rent_basis=bases['owner'],driver_salary_basis=bases['driver'],payment_schedule=schedule,driver_paid_by='operator',driver_provider='operator' if has_driver else 'none')
    rent_calc.validate_versions([v],start,end)
    sides=[s for s in SIDES if s!='driver' or has_driver]
    segs={n:rent_calc.build_segments(rent_calc.versions_entries([v],f,b),start,end) for n,f,b in rent_calc.SIDES if n in sides}
    stub=SimpleNamespace(start_date=start,end_date=end,pk=None)
    out={'days':rent_calc.days(start,end) if end else None,'months':[],'dues':{},'totals':{}}
    m=rent_calc.month_start(start);last=rent_calc.month_start(end) if end else None
    shown=0;totals={n:ZERO for n in sides}
    while shown<60 and (last is None and shown<months or last is not None and m<=last):
        row={n:rent_calc.month_amount(segs[n],m) for n in sides}
        for n in sides:totals[n]+=row[n]
        if len(out['months'])<months:out['months'].append({'month':m.isoformat(),**row})
        m=(m+timedelta(days=32)).replace(day=1);shown+=1
    if end:out['totals']=totals
    today=start
    for n in sides:out['dues'][n]=[{k:(x.isoformat() if isinstance(x,date) else x) for k,x in d.items()} for d in dues_for(stub,n,today=today,until=None if end else start+timedelta(days=200),versions=[v])[:dues_each]]
    return out
