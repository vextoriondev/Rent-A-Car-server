"""Read-only reports. Every figure comes from the ledger (bulk_ledger_statements: one journal query for all periods), so
reports reconcile to statements by construction. Query counts are constant in the number of periods (asserted in tests)."""
import csv
import io
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from django.db.models import CharField, Count, Exists, OuterRef, Sum
from django.db.models.functions import Cast
from . import dues, ranged
from ..i18n import translate, word
from ..models import AgreementVersion, Allocation, Document, ExpenseCategory, Expense, Obligation, Payment, Period
from ..policies import period_in_branch
from .helpers import ZERO, DomainError
from .ranges import entry_dates, overlapping_periods, resolve_range, as_date
from .statements import bulk_ledger_statements

# Column sets match the frontend's current CSV exports (lib/reports.ts).
PERIOD_REPORTS={
    'vehicle-contribution':{'party':'Customer','columns':['earned_rent','owner_share','driver_salary','your_costs','profit'],
        'values':lambda m,c:[m['customer']['billed'],m['owner']['gross'],m['driver']['earned'],m['operator']['operator_borne_costs'],m['operator']['accrual_margin']]},
    'customer-balances':{'party':'Customer','columns':['billed','received','money_to_take'],'values':lambda m,c:[m['customer']['billed'],m['customer']['received'],c['customer']['due']]},
    'owner-liability':{'party':'Owner','columns':['owner_rent','taken_off','paid','money_to_give'],'values':lambda m,c:[m['owner']['gross'],m['owner']['offsets'],m['owner']['paid'],c['owner']['due']]},
    'driver-payroll':{'party':'Driver','columns':['earned','paid_early','paid','money_to_give'],'values':lambda m,c:[m['driver']['earned'],m['driver']['advance'],m['driver']['paid'],c['driver']['due']]},
    'income':{'party':'Customer','columns':['billed','collected','money_to_take','owner_rent','driver_salary','your_costs','profit'],
        'values':lambda m,c:[m['customer']['billed'],m['customer']['received'],c['customer']['due'],m['owner']['gross'],m['driver']['earned'],m['operator']['operator_borne_costs'],m['operator']['accrual_margin']]},
    'cash-movement':{'party':'Customer','columns':['money_in','paid_owners','paid_drivers','cash_left'],'values':lambda m,c:[m['customer']['received'],m['owner']['paid'],m['driver']['paid'],m['operator']['recorded_cash_movement']]},
}
OTHER_REPORTS=('cost-by-responsible','customer-aging','missing-receipts','entries','daily-expenses','category-by-vehicle')
DRIVER_REPORTS={'driver-payroll','cash-movement'}   # reports that include standalone driver agreements
BILL_SIDE={'customer-balances':'customer','owner-liability':'owner','driver-payroll':'driver'}
REPORT_TYPES=list(PERIOD_REPORTS)+list(OTHER_REPORTS)
AGING_BUCKETS=[('aging_current',0),('aging_1_30',30),('aging_31_60',60),('aging_61_90',90),('aging_over_90',None)]

def report_title(slug:str)->str:
    return translate('report_'+slug.replace('-','_'))

def _heading(cid:str,kind:str)->str:
    return translate('col_'+cid)+(' (৳)' if kind=='money' else '')

def _columns(spec):
    """[(id, type)] -> the response fields that describe the columns: headings in the user's language, stable ids and types (text, money, count, date)."""
    return {'columns':[_heading(c,k) for c,k in spec],'column_ids':[c for c,_ in spec],'column_types':[k for _,k in spec]}

def _totals(spec,values):
    return {c:v for (c,k),v in zip(spec,values) if k in('money','count')}

def _page(rows,limit,offset):
    if limit is None:return rows,None
    limit=min(200,max(1,limit));offset=max(0,offset)
    return rows[offset:offset+limit],(offset+limit if offset+limit<len(rows) else None)

def _periods(org,f,upto=None,exact=False):
    """upto=None: the months the filters name (period mode). upto=date: every month up to that day (exact mode, so old balances can show as opening balances)."""
    qs=Period.objects.filter(organization=org).select_related('agreement__vehicle','agreement__customer','driver_agreement__vehicle','driver_agreement__driver')
    if f.get('branch_id'):qs=qs.filter(period_in_branch('',f['branch_id']))
    if exact:
        if upto is not None:qs=qs.filter(month__lte=upto)
    else:
        if f.get('month'):qs=qs.filter(month=f['month'])
        if f.get('month_from'):qs=qs.filter(month__gte=f['month_from'])
        if f.get('month_to'):qs=qs.filter(month__lte=f['month_to'])
        qs=overlapping_periods(qs,as_date(f.get('from')),as_date(f.get('to')))
    if f.get('agreement_id'):qs=qs.filter(agreement_id=f['agreement_id'])
    if f.get('vehicle_id'):qs=qs.filter(agreement__vehicle_id=f['vehicle_id'])
    if f.get('customer_id'):qs=qs.filter(agreement__customer_id=f['customer_id'])
    if f.get('owner_id'):qs=qs.filter(obligations__kind='owner_rent',obligations__party_id=f['owner_id'])
    if f.get('driver_id'):qs=qs.filter(obligations__kind='driver_salary',obligations__party_id=f['driver_id'])
    return qs.order_by('-month','agreement_id')

def exact_window(f):
    """(lo, hi) when the filters ask for exact dates (`from`, `to`); (None, None) otherwise. The month filters (`month`, `month_from`, `month_to`) keep their meaning: whole rent months."""
    return as_date(f.get('from')),as_date(f.get('to'))

def period_ref(p):
    return f'AGR-{p.agreement_id}' if p.agreement_id else f'DRV-{p.driver_agreement_id}'

def _period_vehicle(p):
    v=p.agreement.vehicle if p.agreement_id else p.driver_agreement.vehicle
    return v.registration if v else ''

def _parties(periods):
    """{period_id: {'customer','owner','driver'}} names from the obligations the period was issued with (one query)."""
    out={p.pk:{'customer':p.agreement.customer.name if p.agreement_id else '','owner':'','driver':''} for p in periods}
    for o in Obligation.objects.filter(period__in=periods,kind__in=['owner_rent','driver_salary']).select_related('party'):
        out[o.period_id]['owner' if o.kind=='owner_rent' else 'driver']=o.party.name
    return out

def _money(x):return str(Decimal(x).quantize(Decimal('0.01')))

def run_report(org,rtype,filters=None,limit=100,offset=0,as_of=None):
    """limit=None returns every row (exports). Returns {'report','title','columns','rows','totals','count','next','filters'}; rows are lists aligned with columns."""
    f={k:v for k,v in (filters or {}).items() if v not in(None,'')}
    resolve_range(f.get('from'),f.get('to'))
    if rtype in PERIOD_REPORTS:return _period_report(org,rtype,f,limit,offset)
    if rtype=='cost-by-responsible':return _cost_report(org,f,limit,offset)
    if rtype=='customer-aging':return _aging_report(org,f,limit,offset,as_of or exact_window(f)[1] or date.today())   # a range ages as of its last day
    if rtype=='missing-receipts':return _missing_receipts(org,f,limit,offset)
    if rtype=='entries':return _entries(org,f,limit,offset)
    if rtype=='daily-expenses':return _daily_expenses(org,f,limit,offset)
    if rtype=='category-by-vehicle':return _category_by_vehicle(org,f,limit,offset)
    raise DomainError('unknown_report')

def _period_report(org,rtype,f,limit,offset):
    cfg=PERIOD_REPORTS[rtype]
    lo,hi=exact_window(f);exact=lo is not None or hi is not None
    qs=_periods(org,f,upto=hi,exact=exact)
    if rtype=='driver-payroll':qs=qs.filter(pk__in=Obligation.objects.filter(kind='driver_salary').values('period_id'))  # vehicle-only agreements have no driver side
    if rtype not in DRIVER_REPORTS:qs=qs.filter(agreement__isnull=False)  # standalone driver agreements only appear in the driver reports
    periods=list(qs)
    names=_parties(periods)
    side=BILL_SIDE.get(rtype)
    if exact:
        rs=ranged.ranged_sections(periods,lo,hi)
        views={p.pk:(rs[p.pk]['movements'],rs[p.pk]['closing'],rs[p.pk]['opening']) for p in periods}
    else:
        stmts=bulk_ledger_statements(periods);views={p.pk:(stmts[p.pk],stmts[p.pk],None) for p in periods}
    spec=[('agreement','text'),('vehicle','text'),(cfg['party'].lower(),'text'),('month','date')]+[(c,'money') for c in cfg['columns']]
    opening_col=exact and side is not None   # balance reports also show where each balance stood on the first day
    if opening_col:spec.append(('opening','money'))
    width=len(cfg['columns'])+(1 if opening_col else 0)
    rows=[];states=[];totals=[ZERO]*width
    for p in periods:
        m,c,o=views[p.pk]
        vals=cfg['values'](m,c)
        if opening_col:vals=vals+[o[side]['due'] if o else ZERO]
        if exact and not any(v!=0 for v in vals):continue      # nothing opened, moved or closed in this month
        totals=[t+v for t,v in zip(totals,vals)]
        if side:states.append(c[side]['bill_state'])
        rows.append([period_ref(p),_period_vehicle(p),names[p.pk][cfg['party'].lower()],p.month.isoformat()]+[_money(v) for v in vals])
    page,nxt=_page(rows,limit,offset)
    out={'report':rtype,'title':report_title(rtype),**_columns(spec),'rows':page,'totals':_totals(spec[4:],[_money(t) for t in totals]),'count':len(rows),'next':nxt,'filters':f}
    if states:out['bill_states']=_page(states,limit,offset)[0]  # aligned with rows; the UI chooses the colours
    return out

def _expense_qs(org,f):
    qs=Expense.objects.filter(period__organization=org)
    if f.get('branch_id'):qs=qs.filter(period__agreement__branch_id=f['branch_id'])
    if f.get('month'):qs=qs.filter(period__month=f['month'])
    if f.get('month_from'):qs=qs.filter(period__month__gte=f['month_from'])
    if f.get('month_to'):qs=qs.filter(period__month__lte=f['month_to'])
    if f.get('agreement_id'):qs=qs.filter(period__agreement_id=f['agreement_id'])
    if f.get('vehicle_id'):qs=qs.filter(period__agreement__vehicle_id=f['vehicle_id'])
    if f.get('customer_id'):qs=qs.filter(period__agreement__customer_id=f['customer_id'])
    qs=entry_dates(qs,as_date(f.get('from')),as_date(f.get('to')))
    if f.get('date'):qs=qs.filter(event_date=as_date(f['date']))
    for key in('category','paid_by','responsible','state'):
        if f.get(key):qs=qs.filter(**{key:f[key]})
    return qs

def _cost_report(org,f,limit,offset):
    qs=_expense_qs(org,f).filter(state=Expense.State.POSTED).values('responsible','category').annotate(n=Count('id'),amount=Sum('amount')).order_by('responsible','category')
    rows=[[word('party',r['responsible']),word('category',r['category']),r['n'],_money(r['amount'])] for r in qs]
    total=sum((Decimal(r[3]) for r in rows),ZERO);page,nxt=_page(rows,limit,offset)
    spec=[('responsible','text'),('category','text'),('entries','count'),('amount','money')]
    return {'report':'cost-by-responsible','title':report_title('cost-by-responsible'),**_columns(spec),'rows':page,'totals':{'amount':_money(total)},'count':len(rows),'next':nxt,'filters':f}

def _custom_due_dates(periods,as_of):
    """{agreement_id: oldest unpaid customer due date (or None when none is due yet)} for agreements whose schedule is not the month-end default."""
    ids={p.agreement_id for p in periods if p.agreement_id}
    if not ids:return {}
    by={}
    for v in AgreementVersion.objects.filter(agreement_id__in=ids).exclude(status='draft').order_by('effective_from','number'):by.setdefault(v.agreement_id,[]).append(v)
    out={}
    agreements={p.agreement_id:p.agreement for p in periods if p.agreement_id}
    for aid,vs in by.items():
        if not dues.is_default(vs,'customer'):out[aid]=dues.oldest_unpaid_due_date(agreements[aid],'customer',vs,as_of)
    return out

def _aging_report(org,f,limit,offset,as_of):
    """Outstanding customer balance per customer on one date, aged from the end of the billing month (or the oldest unpaid due date for a custom schedule)."""
    periods=list(_periods(org,f,upto=as_of,exact=True));names=_parties(periods)
    sections=ranged.ranged_sections(periods,None,as_of)   # balances as they stood on the date, accruing only the days up to it
    buckets={};oldest={}
    custom=_custom_due_dates(periods,as_of)   # agreements with their own customer payment schedule age from their oldest unpaid due date
    for p in periods:
        due=sections[p.pk]['closing']['customer']['due']
        if due<=0:continue
        month_end=(p.month.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        days=(as_of-month_end).days
        if p.agreement_id in custom:days=(as_of-custom[p.agreement_id]).days if custom[p.agreement_id] else 0
        idx=0 if days<=0 else next((i for i,(_,hi) in enumerate(AGING_BUCKETS) if hi is not None and days<=hi),len(AGING_BUCKETS)-1)
        name=names[p.pk]['customer'];row=buckets.setdefault(name,[ZERO]*len(AGING_BUCKETS));row[idx]+=due
        oldest[name]=max(oldest.get(name,0),days)
    rows=[[name]+[_money(v) for v in vals]+[_money(sum(vals,ZERO)),max(oldest[name],0)] for name,vals in sorted(buckets.items())]
    totals=[sum((v[i] for v in buckets.values()),ZERO) for i in range(len(AGING_BUCKETS))]
    page,nxt=_page(rows,limit,offset)
    spec=[('customer','text')]+[(b,'money') for b,_ in AGING_BUCKETS]+[('total','money'),('days_overdue','count')]
    return {'report':'customer-aging','title':report_title('customer-aging'),**_columns(spec),'rows':page,'totals':_totals(spec[1:-1],[_money(t) for t in totals]+[_money(sum(totals,ZERO))]),'count':len(rows),'next':nxt,'filters':{**f,'as_of':as_of.isoformat()}}

def _missing_receipts(org,f,limit,offset):
    has_doc=Document.objects.filter(organization=org,target_type='expense',target_id=OuterRef('pk_str'))
    qs=_expense_qs(org,f).exclude(state__in=[Expense.State.REVERSED,Expense.State.DRAFT]).filter(receipt_reference='').select_related('period__agreement__vehicle')
    qs=qs.annotate(pk_str=Cast('pk',CharField())).annotate(has_doc=Exists(has_doc)).filter(has_doc=False).order_by('-event_date','-id')
    rows=[[e.public_id,f'AGR-{e.period.agreement_id}',e.period.agreement.vehicle.registration,e.event_date.isoformat(),word('category',e.category),_money(e.amount),e.reference,word('state',e.state)] for e in qs]
    page,nxt=_page(rows,limit,offset)
    spec=[('id','text'),('agreement','text'),('vehicle','text'),('date','date'),('category','text'),('amount','money'),('reference','text'),('status','text')]
    return {'report':'missing-receipts','title':report_title('missing-receipts'),**_columns(spec),'rows':page,'totals':{'amount':_money(sum((Decimal(r[5]) for r in rows),ZERO))},'count':len(rows),'next':nxt,'filters':f}

def _entries(org,f,limit,offset):
    """Same columns as the frontend entries export: ID, Agreement, Date, Type, Amount BDT, Paid by, Responsible, Status, Reference."""
    rows=[]
    for e in _expense_qs(org,f).exclude(state=Expense.State.DRAFT).select_related('period').order_by('-event_date','-id'):
        rows.append((e.event_date,e.pk,[e.public_id,f'AGR-{e.period.agreement_id}',e.event_date.isoformat(),word('entrytype','expense'),_money(e.amount),word('party',e.paid_by),word('party',e.responsible),word('state',e.state),e.reference]))
    pq=Payment.objects.filter(organization=org)
    if f.get('branch_id'):pq=pq.filter(pk__in=Allocation.objects.filter(period_in_branch('obligation__period__',f['branch_id'])).values('payment_id'))
    pq=entry_dates(pq,as_date(f.get('from')),as_date(f.get('to'))).prefetch_related('allocations__obligation__period')
    for p in pq:
        periods={a.obligation.period for a in p.allocations.all()}
        if f.get('month') and not any(x.month==f['month'] for x in periods):continue
        if f.get('agreement_id') and not any(x.agreement_id==int(f['agreement_id']) for x in periods):continue
        label=word('entrytype',p.kind)
        agreement=','.join(sorted({period_ref(x) for x in periods}))
        rows.append((p.event_date,p.pk,[p.public_id,agreement,p.event_date.isoformat(),label,_money(p.amount),p.party.name if p.kind=='customer_payment' else word('party','operator'),word('party','operator'),word('state','posted'),p.reference]))
    rows.sort(key=lambda r:(r[0],r[1]),reverse=True);flat=[r[2] for r in rows]
    page,nxt=_page(flat,limit,offset)
    spec=[('id','text'),('agreement','text'),('date','date'),('type','text'),('amount','money'),('paid_by','text'),('responsible','text'),('status','text'),('reference','text')]
    return {'report':'entries','title':report_title('entries'),**_columns(spec),'rows':page,'totals':{},'count':len(flat),'next':nxt,'filters':f}

def _has_receipt(qs,org):
    doc=Document.objects.filter(organization=org,target_type='expense',target_id=OuterRef('pk_str'))
    return qs.annotate(pk_str=Cast('pk',CharField())).annotate(has_doc=Exists(doc))

def _daily_expenses(org,f,limit,offset):
    """One row per expense recorded for the day (drafts excluded unless asked for by state); totals count posted expenses only so they reconcile to the ledger."""
    if not f.get('date'):raise DomainError('a_date_is_required')
    qs=_has_receipt(_expense_qs(org,f),org).select_related('period__agreement__vehicle')
    if not f.get('state'):qs=qs.exclude(state=Expense.State.DRAFT)
    items=list(qs.order_by('id'))
    rows=[[e.period.agreement.vehicle.registration,f'AGR-{e.period.agreement_id}',word('category',e.category),_money(e.amount),word('party',e.paid_by),word('party',e.responsible),word('state',e.state),e.reference,translate('yes') if (e.receipt_reference or e.has_doc) else translate('no')] for e in items]
    posted=[e for e in items if e.state==Expense.State.POSTED]
    def by(key):
        out={}
        for e in posted:out[key(e)]=out.get(key(e),ZERO)+e.amount
        return {k:_money(v) for k,v in sorted(out.items())}
    page,nxt=_page(rows,limit,offset)
    spec=[('vehicle','text'),('agreement','text'),('category','text'),('amount','money'),('paid_by','text'),('responsible','text'),('status','text'),('reference','text'),('receipt','text')]
    return {'report':'daily-expenses','title':report_title('daily-expenses'),**_columns(spec),'rows':page,'totals':{'amount':_money(sum((e.amount for e in posted),ZERO))},
        'breakdowns':{'by_category':by(lambda e:word('category',e.category)),'by_responsible':by(lambda e:word('party',e.responsible))},'count':len(rows),'next':nxt,'filters':f}

def _category_by_vehicle(org,f,limit,offset):
    """Posted cost of one category per vehicle for any range of entry dates; a deactivated category still works (history)."""
    if not f.get('category'):raise DomainError('a_category_is_required')
    cat=ExpenseCategory.objects.filter(organization=org,code=f['category']).first()
    if not cat:raise DomainError('unknown_expense_category')
    qs=_expense_qs(org,{k:v for k,v in f.items() if k not in('state',)}).filter(state=Expense.State.POSTED)
    agg=qs.values('period__agreement__vehicle__registration','period__agreement__vehicle__model_name').annotate(n=Count('id'),amount=Sum('amount')).order_by('period__agreement__vehicle__registration')
    rows=[[r['period__agreement__vehicle__registration'],r['period__agreement__vehicle__model_name'],r['n'],_money(r['amount'])] for r in agg]
    total=sum((Decimal(r[3]) for r in rows),ZERO);page,nxt=_page(rows,limit,offset)
    spec=[('vehicle','text'),('model','text'),('entries','count'),('amount','money')]
    return {'report':'category-by-vehicle','title':report_title('category-by-vehicle')+': '+cat.name,**_columns(spec),'rows':page,'totals':{'amount':_money(total)},'count':len(rows),'next':nxt,'filters':f}

_FORMULA=re.compile(r'^(?:[=+@\t\r]|-(?![\d.]))')

def excel_safe(cell):
    """Excel runs a text cell that starts with = + @ (or - not followed by a digit) as a formula, so a typed reference such as =HYPERLINK(...) could execute when the file is opened. A leading apostrophe-free fix is a single quote prefix; numbers like -1500.00 are left alone."""
    return "'"+cell if isinstance(cell,str) and _FORMULA.match(cell) else cell

def report_csv(org,rtype,filters=None,as_of=None):
    """CSV text: report name, generated_at and the applied filters first, then the table. Exports ignore pagination (all rows)."""
    data=run_report(org,rtype,filters,limit=None,as_of=as_of)
    buf=io.StringIO(newline='');w=csv.writer(buf,lineterminator='\r\n',quoting=csv.QUOTE_ALL)
    now=datetime.now(timezone.utc).isoformat()
    w.writerow([translate('csv_report'),data['title']]);w.writerow([translate('csv_generated_at'),now])
    w.writerow([translate('csv_filters'),'; '.join(f'{k}={v}' for k,v in sorted(data['filters'].items())) or translate('csv_none')]);w.writerow([])
    w.writerow(data['columns']+([translate('csv_generated_at')] if rtype in PERIOD_REPORTS else []))
    for r in data['rows']:w.writerow([excel_safe(c) for c in r]+([now] if rtype in PERIOD_REPORTS else []))
    for name,part in data.get('breakdowns',{}).items():
        w.writerow([]);w.writerow([word('breakdown',name),_heading('amount','money')])
        for k,v in part.items():w.writerow([excel_safe(k),v])
    return '﻿'+buf.getvalue()
