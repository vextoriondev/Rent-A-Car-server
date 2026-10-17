"""Date ranges for any-period inspection (M13). Two meanings, never mixed:
entry-date filters use the exact dates (`event_date`); period filters use every rent period that overlaps the range (not prorated)."""
from datetime import date
from .helpers import DomainError

MAX_DAYS=5*366

def as_date(value):
    if value in(None,''):return None
    if isinstance(value,date):return value
    try:return date.fromisoformat(str(value))
    except ValueError as exc:raise DomainError('dates_must_be_written_as_yyyy_mm') from exc

def resolve_range(start=None,end=None):
    """(start, end) as dates; both optional, `end` inclusive. Rejects start > end and ranges over 5 years."""
    start,end=as_date(start),as_date(end)
    if start and end:
        if start>end:raise DomainError('the_start_date_is_after_the_end')
        if (end-start).days>MAX_DAYS:raise DomainError('a_range_can_span_at_most_5')
    return start,end

def overlapping_periods(qs,start,end,field='month'):
    """Periods are keyed by their first day, so a period overlaps [start, end] when it starts on or before `end` and ends on or after `start`."""
    if start:qs=qs.filter(**{field+'__gte':start.replace(day=1)})
    if end:qs=qs.filter(**{field+'__lte':end})
    return qs

def entry_dates(qs,start,end,field='event_date'):
    if start:qs=qs.filter(**{field+'__gte':start})
    if end:qs=qs.filter(**{field+'__lte':end})
    return qs
