"""Read-only reports. Every figure comes from the ledger (bulk_ledger_statements: one journal query for all periods), so
reports reconcile to statements by construction. Query counts are constant in the number of periods (asserted in tests)."""
import csv
import io
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from django.db.models import CharField, Count, Exists, OuterRef, Sum
from django.db.models.functions import Cast
from ..models import Document, Expense, Obligation, Payment, Period
from .helpers import ZERO, DomainError
from .statements import bulk_ledger_statements

# Column sets match the frontend's current CSV exports (lib/reports.ts).
PERIOD_REPORTS={
    'vehicle-contribution':{'title':'Vehicle contribution','party':'Customer','columns':['Earned rent','Owner gross','Driver salary','Operator costs','Accrual margin'],
        'values':lambda s:[s['customer']['billed'],s['owner']['gross'],s['driver']['earned'],s['operator']['operator_borne_costs'],s['operator']['accrual_margin']]},
    'customer-balances':{'title':'Customer balances','party':'Customer','columns':['Billed','Received','Outstanding'],'values':lambda s:[s['customer']['billed'],s['customer']['received'],s['customer']['due']]},
    'owner-liability':{'title':'Owner liability','party':'Owner','columns':['Gross','Offsets','Paid','Due'],'values':lambda s:[s['owner']['gross'],s['owner']['offsets'],s['owner']['paid'],s['owner']['due']]},
    'driver-payroll':{'title':'Driver payroll','party':'Driver','columns':['Earned','Advance','Paid','Due'],'values':lambda s:[s['driver']['earned'],s['driver']['advance'],s['driver']['paid'],s['driver']['due']]},
    'cash-movement':{'title':'Cash movement','party':'Customer','columns':['Receipts','Owner paid','Driver paid','Net cash'],'values':lambda s:[s['customer']['received'],s['owner']['paid'],s['driver']['paid'],s['operator']['recorded_cash_movement']]},
}
OTHER_REPORTS={'cost-by-responsible':'Cost by responsible party','customer-aging':'Customer aging','missing-receipts':'Missing receipts','entries':'Entries'}
REPORT_TYPES=list(PERIOD_REPORTS)+list(OTHER_REPORTS)
AGING_BUCKETS=[('Current',0),('1-30 days',30),('31-60 days',60),('61-90 days',90),('Over 90 days',None)]

def _page(rows,limit,offset):
    if limit is None:return rows,None
    limit=min(200,max(1,limit));offset=max(0,offset)
    return rows[offset:offset+limit],(offset+limit if offset+limit<len(rows) else None)

def _periods(org,f):
    qs=Period.objects.filter(organization=org).select_related('agreement__vehicle','agreement__customer')
    if f.get('month'):qs=qs.filter(month=f['month'])
    if f.get('month_from'):qs=qs.filter(month__gte=f['month_from'])
    if f.get('month_to'):qs=qs.filter(month__lte=f['month_to'])
    if f.get('agreement_id'):qs=qs.filter(agreement_id=f['agreement_id'])
    if f.get('vehicle_id'):qs=qs.filter(agreement__vehicle_id=f['vehicle_id'])
    if f.get('customer_id'):qs=qs.filter(agreement__customer_id=f['customer_id'])
    if f.get('owner_id'):qs=qs.filter(obligations__kind='owner_rent',obligations__party_id=f['owner_id'])
    if f.get('driver_id'):qs=qs.filter(obligations__kind='driver_salary',obligations__party_id=f['driver_id'])
    return qs.order_by('-month','agreement_id')

def _parties(periods):
    """{period_id: {'customer','owner','driver'}} names from the obligations the period was issued with (one query)."""
    out={p.pk:{'customer':p.agreement.customer.name,'owner':'','driver':''} for p in periods}
    for o in Obligation.objects.filter(period__in=periods,kind__in=['owner_rent','driver_salary']).select_related('party'):
        out[o.period_id]['owner' if o.kind=='owner_rent' else 'driver']=o.party.name
    return out

def _money(x):return str(Decimal(x).quantize(Decimal('0.01')))

def run_report(org,rtype,filters=None,limit=100,offset=0,as_of=None):
    """limit=None returns every row (exports). Returns {'report','title','columns','rows','totals','count','next','filters'}; rows are lists aligned with columns."""
    f={k:v for k,v in (filters or {}).items() if v not in(None,'')}
    if rtype in PERIOD_REPORTS:return _period_report(org,rtype,f,limit,offset)
    if rtype=='cost-by-responsible':return _cost_report(org,f,limit,offset)
    if rtype=='customer-aging':return _aging_report(org,f,limit,offset,as_of or date.today())
    if rtype=='missing-receipts':return _missing_receipts(org,f,limit,offset)
    if rtype=='entries':return _entries(org,f,limit,offset)
    raise DomainError('Unknown report')

def _period_report(org,rtype,f,limit,offset):
    cfg=PERIOD_REPORTS[rtype];periods=list(_periods(org,f))
    stmts=bulk_ledger_statements(periods);names=_parties(periods)
    columns=['Agreement','Vehicle',cfg['party'],'Month']+[c+' BDT' for c in cfg['columns']]
    rows=[];totals=[ZERO]*len(cfg['columns'])
    for p in periods:
        vals=cfg['values'](stmts[p.pk]);totals=[t+v for t,v in zip(totals,vals)]
        rows.append([f'AGR-{p.agreement_id}',p.agreement.vehicle.registration,names[p.pk][cfg['party'].lower()],p.month.isoformat()]+[_money(v) for v in vals])
    page,nxt=_page(rows,limit,offset)
    return {'report':rtype,'title':cfg['title'],'columns':columns,'rows':page,'totals':dict(zip([c+' BDT' for c in cfg['columns']],[_money(t) for t in totals])),'count':len(rows),'next':nxt,'filters':f}

def _expense_qs(org,f):
    qs=Expense.objects.filter(period__organization=org)
    if f.get('month'):qs=qs.filter(period__month=f['month'])
    if f.get('month_from'):qs=qs.filter(period__month__gte=f['month_from'])
    if f.get('month_to'):qs=qs.filter(period__month__lte=f['month_to'])
    if f.get('agreement_id'):qs=qs.filter(period__agreement_id=f['agreement_id'])
    if f.get('vehicle_id'):qs=qs.filter(period__agreement__vehicle_id=f['vehicle_id'])
    if f.get('customer_id'):qs=qs.filter(period__agreement__customer_id=f['customer_id'])
    return qs

def _cost_report(org,f,limit,offset):
    qs=_expense_qs(org,f).filter(state=Expense.State.POSTED).values('responsible','category').annotate(n=Count('id'),amount=Sum('amount')).order_by('responsible','category')
    rows=[[r['responsible'].title(),r['category'].replace('_',' ').title(),r['n'],_money(r['amount'])] for r in qs]
    total=sum((Decimal(r[3]) for r in rows),ZERO);page,nxt=_page(rows,limit,offset)
    return {'report':'cost-by-responsible','title':OTHER_REPORTS['cost-by-responsible'],'columns':['Responsible','Category','Entries','Amount BDT'],'rows':page,'totals':{'Amount BDT':_money(total)},'count':len(rows),'next':nxt,'filters':f}

def _aging_report(org,f,limit,offset,as_of):
    """Outstanding customer balance per customer, aged from the end of the billing month."""
    periods=list(_periods(org,f));stmts=bulk_ledger_statements(periods);names=_parties(periods)
    buckets={}
    for p in periods:
        due=stmts[p.pk]['customer']['due']
        if due<=0:continue
        month_end=(p.month.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        days=(as_of-month_end).days
        idx=0 if days<=0 else next((i for i,(_,hi) in enumerate(AGING_BUCKETS) if hi is not None and days<=hi),len(AGING_BUCKETS)-1)
        row=buckets.setdefault(names[p.pk]['customer'],[ZERO]*len(AGING_BUCKETS));row[idx]+=due
    rows=[[name]+[_money(v) for v in vals]+[_money(sum(vals,ZERO))] for name,vals in sorted(buckets.items())]
    totals=[sum((v[i] for v in buckets.values()),ZERO) for i in range(len(AGING_BUCKETS))]
    page,nxt=_page(rows,limit,offset)
    cols=['Customer']+[b+' BDT' for b,_ in AGING_BUCKETS]+['Total BDT']
    return {'report':'customer-aging','title':OTHER_REPORTS['customer-aging'],'columns':cols,'rows':page,'totals':dict(zip(cols[1:],[_money(t) for t in totals]+[_money(sum(totals,ZERO))])),'count':len(rows),'next':nxt,'filters':{**f,'as_of':as_of.isoformat()}}

def _missing_receipts(org,f,limit,offset):
    has_doc=Document.objects.filter(organization=org,target_type='expense',target_id=OuterRef('pk_str'))
    qs=_expense_qs(org,f).exclude(state__in=[Expense.State.REVERSED,Expense.State.DRAFT]).filter(receipt_reference='').select_related('period__agreement__vehicle')
    qs=qs.annotate(pk_str=Cast('pk',CharField())).annotate(has_doc=Exists(has_doc)).filter(has_doc=False).order_by('-event_date','-id')
    rows=[[e.public_id,f'AGR-{e.period.agreement_id}',e.period.agreement.vehicle.registration,e.event_date.isoformat(),e.category.replace('_',' ').title(),_money(e.amount),e.reference,e.state.title()] for e in qs]
    page,nxt=_page(rows,limit,offset)
    return {'report':'missing-receipts','title':OTHER_REPORTS['missing-receipts'],'columns':['ID','Agreement','Vehicle','Date','Category','Amount BDT','Reference','Status'],'rows':page,'totals':{'Amount BDT':_money(sum((Decimal(r[5]) for r in rows),ZERO))},'count':len(rows),'next':nxt,'filters':f}

def _entries(org,f,limit,offset):
    """Same columns as the frontend entries export: ID, Agreement, Date, Type, Amount BDT, Paid by, Responsible, Status, Reference."""
    rows=[]
    for e in _expense_qs(org,f).exclude(state=Expense.State.DRAFT).select_related('period').order_by('-event_date','-id'):
        rows.append((e.event_date,e.pk,[e.public_id,f'AGR-{e.period.agreement_id}',e.event_date.isoformat(),'Expense',_money(e.amount),e.paid_by.title(),e.responsible.title(),{'pending':'Pending approval','posted':'Posted','reversed':'Reversed'}[e.state],e.reference]))
    pq=Payment.objects.filter(organization=org).prefetch_related('allocations__obligation__period')
    for p in pq:
        periods={a.obligation.period for a in p.allocations.all()}
        if f.get('month') and not any(x.month==f['month'] for x in periods):continue
        if f.get('agreement_id') and not any(x.agreement_id==int(f['agreement_id']) for x in periods):continue
        label={'customer_payment':'Customer payment','owner_payout':'Owner payout','driver_payout':'Driver payout','salary_advance':'Salary advance'}[p.kind]
        agreement=','.join(sorted({f'AGR-{x.agreement_id}' for x in periods}))
        rows.append((p.event_date,p.pk,[p.public_id,agreement,p.event_date.isoformat(),label,_money(p.amount),p.party.name if p.kind=='customer_payment' else 'Operator','Operator','Posted',p.reference]))
    rows.sort(key=lambda r:(r[0],r[1]),reverse=True);flat=[r[2] for r in rows]
    page,nxt=_page(flat,limit,offset)
    return {'report':'entries','title':OTHER_REPORTS['entries'],'columns':['ID','Agreement','Date','Type','Amount BDT','Paid by','Responsible','Status','Reference'],'rows':page,'totals':{},'count':len(flat),'next':nxt,'filters':f}

_FORMULA=re.compile(r'^(?:[=+@\t\r]|-(?![\d.]))')

def excel_safe(cell):
    """Excel runs a text cell that starts with = + @ (or - not followed by a digit) as a formula, so a typed reference such as =HYPERLINK(...) could execute when the file is opened. A leading apostrophe-free fix is a single quote prefix; numbers like -1500.00 are left alone."""
    return "'"+cell if isinstance(cell,str) and _FORMULA.match(cell) else cell

def report_csv(org,rtype,filters=None,as_of=None):
    """CSV text: report name, generated_at and the applied filters first, then the table. Exports ignore pagination (all rows)."""
    data=run_report(org,rtype,filters,limit=None,as_of=as_of)
    buf=io.StringIO(newline='');w=csv.writer(buf,lineterminator='\r\n',quoting=csv.QUOTE_ALL)
    now=datetime.now(timezone.utc).isoformat()
    w.writerow(['Report',data['title']]);w.writerow(['Generated at',now])
    w.writerow(['Filters','; '.join(f'{k}={v}' for k,v in sorted(data['filters'].items())) or 'none']);w.writerow([])
    w.writerow(data['columns']+(['Generated at'] if rtype in PERIOD_REPORTS else []))
    for r in data['rows']:w.writerow([excel_safe(c) for c in r]+([now] if rtype in PERIOD_REPORTS else []))
    return '﻿'+buf.getvalue()
