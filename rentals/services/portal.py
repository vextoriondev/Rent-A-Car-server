"""Party profile pages (M10), viewed by staff. No party logins, no automatic sending.

Every projection is chosen by the party's *kind*, never by the viewer's role: a customer page holds only the customer side of each month,
an owner page only the owner side, a driver page only the driver side. Names, rents and terms of the other parties are never read here."""
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from ..models import Agreement, Charge, DriverAgreement, DriverAssignment, Document, NotificationOutbox, Obligation, OwnerOffset, Party, Payment, Period, Vehicle, VehicleOwnership
from django.db.models import Sum
from .driver_agreements import driver_balance_split
from .documents import can_access, list_documents
from .helpers import ZERO, DomainError
from .ranges import overlapping_periods, resolve_range
from .reports import excel_safe
from .statements import bulk_ledger_statements, customer_bill_state, payable_bill_state

FIELDS={
    'customer':['billed','received','carried_forward','due'],
    'owner':['gross','offsets','net','paid','carried_forward','due'],
    'driver':['earned','advance','paid','carried_forward','due'],
}
LABELS={'billed':'Billed BDT','received':'Received BDT','carried_forward':'Carried forward BDT','due':'Due BDT','gross':'Gross rent BDT','offsets':'Offsets BDT','net':'Net rent BDT','paid':'Paid BDT','earned':'Earned BDT','advance':'Advance BDT'}
KIND_LABEL={'customer_payment':'Customer payment','owner_payout':'Owner payout','driver_payout':'Driver payout','salary_advance':'Salary advance'}
OBLIGATION_LABEL={'customer_rent':'Customer rent','owner_rent':'Owner rent','driver_salary':'Driver salary'}

def _state(kind,due):return customer_bill_state(due) if kind=='customer' else payable_bill_state(due)
def _ref(p):return f'AGR-{p.agreement_id}' if p.agreement_id else f'DRV-{p.driver_agreement_id}'
def _vehicle(p):
    v=p.agreement.vehicle if p.agreement_id else p.driver_agreement.vehicle
    return v.registration if v else ''
def _page(items,limit,offset):
    limit=min(200,max(1,limit));offset=max(0,offset)
    return {'count':len(items),'items':items[offset:offset+limit],'next':offset+limit if offset+limit<len(items) else None}

def party_periods(org,party,start=None,end=None):
    """Months in which the party has an obligation (their side exists), newest last."""
    qs=Period.objects.filter(organization=org,obligations__party=party).distinct().select_related('agreement__vehicle','driver_agreement__vehicle')
    return list(overlapping_periods(qs,start,end).order_by('month','pk'))

def _rows(party,periods):
    side=party.kind
    stmts=bulk_ledger_statements(periods)
    rows=[]
    for p in periods:
        s=stmts[p.pk][side]
        rows.append({'period_id':p.pk,'month':p.month.isoformat(),'ref':_ref(p),'vehicle':_vehicle(p),**{f:s[f] for f in FIELDS[side]},'bill_state':_state(side,s['due'])})
    return rows

def _totals(party,rows):
    t={f:sum((r[f] for r in rows),ZERO) for f in FIELDS[party.kind]}
    return {**t,'bill_state':_state(party.kind,t['due'])}

def portal_statement(org,party,start=None,end=None):
    start,end=resolve_range(start,end)
    rows=_rows(party,party_periods(org,party,start,end))
    return {'kind':party.kind,'from':start.isoformat() if start else None,'to':end.isoformat() if end else None,'rows':rows,'totals':_totals(party,rows)}

def portal_profile(org,party):
    kind=party.kind
    data={'party':{'id':party.pk,'kind':kind,'name':party.name,'phone':party.phone,'email':party.email,'contacts':[{'id':c.pk,'name':c.name,'phone':c.phone,'email':c.email,'role':c.role} for c in party.contacts.all()]}}
    rows=_rows(party,party_periods(org,party))
    data['summary']={'kind':kind,**_totals(party,rows)}
    if kind=='customer':
        data['agreements']=[{'ref':f'AGR-{a.pk}','vehicle':a.vehicle.registration,'model_name':a.vehicle.model_name,'start_date':a.start_date.isoformat(),'end_date':a.end_date.isoformat() if a.end_date else None,'status':a.status,'customer_rent':a.customer_rent}
            for a in Agreement.objects.filter(organization=org,customer=party).select_related('vehicle').order_by('pk')]
        data['vehicles']=sorted({a['vehicle'] for a in data['agreements']})
        data['vehicles']=[{'registration':r} for r in data['vehicles']]
    elif kind=='owner':
        ids=set(Vehicle.objects.filter(organization=org,owner=party).values_list('pk',flat=True))|set(VehicleOwnership.objects.filter(organization=org,owner=party).values_list('vehicle_id',flat=True))
        data['vehicles']=[{'id':v.pk,'registration':v.registration,'model_name':v.model_name} for v in Vehicle.objects.filter(pk__in=ids).order_by('registration')]
        data['agreements']=[{'ref':f'AGR-{a.pk}','vehicle':a.vehicle.registration,'start_date':a.start_date.isoformat(),'end_date':a.end_date.isoformat() if a.end_date else None,'status':a.status,'owner_rent':a.owner_rent}
            for a in Agreement.objects.filter(organization=org,vehicle_id__in=ids).select_related('vehicle').order_by('pk')]
    else:
        assigned=DriverAssignment.objects.filter(organization=org,driver=party).select_related('agreement__vehicle').order_by('effective_from')
        data['agreements']=[{'ref':f'AGR-{x.agreement_id}','vehicle':x.agreement.vehicle.registration,'effective_from':x.effective_from.isoformat(),'effective_to':x.effective_to.isoformat() if x.effective_to else None,'salary':x.salary_override if x.salary_override is not None else x.agreement.driver_salary,'status':x.agreement.status} for x in assigned]
        data['vehicles']=[{'registration':r} for r in sorted({a['vehicle'] for a in data['agreements']})]
        data['driver_agreements']=[{'ref':f'DRV-{d.pk}','vehicle':d.vehicle.registration if d.vehicle_id else None,'start_date':d.start_date.isoformat(),'end_date':d.end_date.isoformat() if d.end_date else None,'salary':d.salary,'terms':d.terms,'status':d.status}
            for d in DriverAgreement.objects.filter(organization=org,driver=party).select_related('vehicle').order_by('pk')]
        data['split']=driver_balance_split(org,party.pk)
        data['split']={k:({'due':v['due'],'items':[{**i} for i in v['items']]} if isinstance(v,dict) else v) for k,v in data['split'].items()}
    return data

def portal_obligations(org,party,limit=100,offset=0):
    qs=Obligation.objects.filter(party=party,period__organization=org).exclude(kind='carry_out').select_related('period__agreement','period__driver_agreement').annotate(allocated=Sum('allocations__amount')).order_by('-period__month','-pk')
    notes={c.obligation_id:c.description for c in Charge.objects.filter(organization=org,party=party).exclude(obligation=None)}
    cents=Decimal('0.01')
    offsets={r['obligation_id']:r['total'] for r in OwnerOffset.objects.filter(obligation__party=party,expense__state='posted').values('obligation_id').annotate(total=Sum('amount'))}
    items=[]
    for o in qs:
        paid=(o.allocated or ZERO).quantize(cents)
        deducted=(offsets.get(o.pk) or ZERO).quantize(cents)   # owner offsets also reduce what is still open
        items.append({'id':o.pk,'period_id':o.period_id,'month':o.period.month.isoformat(),'ref':_ref(o.period),'kind':o.kind,'description':OBLIGATION_LABEL.get(o.kind) or o.get_kind_display()+(f': {notes[o.pk]}' if notes.get(o.pk) else ''),'amount':o.amount,'allocated':paid,'open_amount':max(o.amount-paid-deducted,ZERO)})
    return _page(items,limit,offset)

def portal_payments(org,party,limit=100,offset=0):
    qs=Payment.objects.filter(organization=org,party=party).prefetch_related('allocations__obligation__period').order_by('-event_date','-pk')
    items=[{'id':p.pk,'public_id':p.public_id,'kind':p.kind,'label':KIND_LABEL[p.kind],'amount':p.amount,'reference':p.reference,'method':p.method,'event_date':p.event_date.isoformat(),
        'allocations':[{'obligation_id':a.obligation_id,'month':a.obligation.period.month.isoformat(),'amount':a.amount} for a in p.allocations.all()]} for p in qs]
    return _page(items,limit,offset)

def portal_deductions(org,member,party,limit=100,offset=0):
    """Owner offsets: what was deducted from the owner's rent and why, with the receipt files the viewer may open."""
    if party.kind!='owner':raise DomainError('Deductions exist for owners only')
    qs=OwnerOffset.objects.filter(obligation__party=party,obligation__period__organization=org).select_related('expense__period__agreement__vehicle').order_by('-expense__event_date','-pk')
    offsets=list(qs)
    docs={}
    for d in Document.objects.filter(organization=org,target_type='expense',target_id__in=[str(o.expense_id) for o in offsets]).order_by('id'):
        if can_access(member,d):docs.setdefault(d.target_id,[]).append({'id':d.pk,'name':d.original_name,'content_type':d.content_type})
    items=[]
    for o in offsets:
        e=o.expense
        items.append({'expense_id':e.public_id,'category':e.category,'amount':o.amount,'state':e.state,'event_date':e.event_date.isoformat(),'reason':e.description or e.category.replace('_',' ').capitalize(),'reference':e.reference,
            'receipt_reference':e.receipt_reference,'month':e.period.month.isoformat(),'ref':_ref(e.period),'vehicle':_vehicle(e.period),'proof':docs.get(str(e.pk),[])})
    return _page(items,limit,offset)

def portal_activity(org,party,limit=100,offset=0):
    """One timeline: payments, months issued and charges for this party. Nothing from other parties' sides."""
    items=[]
    for p in Payment.objects.filter(organization=org,party=party):
        items.append((p.event_date,p.pk,{'date':p.event_date.isoformat(),'type':'payment','text':f'{KIND_LABEL[p.kind]} recorded','amount':p.amount,'ref':p.reference}))
    for o in Obligation.objects.filter(party=party,period__organization=org,kind__in=list(OBLIGATION_LABEL)).select_related('period__agreement','period__driver_agreement'):
        d=o.created_at.date()
        items.append((d,o.pk,{'date':d.isoformat(),'type':'month_issued','text':f'{OBLIGATION_LABEL[o.kind]} issued for {o.period.month:%B %Y}','amount':o.amount,'ref':_ref(o.period)}))
    for c in Charge.objects.filter(organization=org,party=party).exclude(kind='carry_out'):
        d=c.created_at.date()
        items.append((d,c.pk,{'date':d.isoformat(),'type':'charge','text':f"{c.get_kind_display()} ({c.direction}): {c.description or 'no note'}",'amount':c.amount,'ref':c.reference}))
    items.sort(key=lambda x:(x[0],x[1]),reverse=True)
    return _page([x[2] for x in items],limit,offset)

def portal_documents(org,member,party):
    return list_documents(org,member,'party',party.pk)

def portal_notices(org,party,limit=100,offset=0):
    qs=NotificationOutbox.objects.filter(organization=org,recipient=party).order_by('-id')
    return _page(list(qs),limit,offset)

def portal_statement_csv(org,party,start=None,end=None):
    """Same rules as the report exports: BOM, a preamble naming the page and range, formula-safe cells, party's own side only."""
    data=portal_statement(org,party,start,end)
    fields=FIELDS[party.kind]
    buf=io.StringIO(newline='');w=csv.writer(buf,lineterminator='\r\n',quoting=csv.QUOTE_ALL)
    w.writerow(['Statement',excel_safe(party.name)]);w.writerow(['Kind',party.kind]);w.writerow(['Generated at',datetime.now(timezone.utc).isoformat()])
    w.writerow(['Period',f"{data['from'] or 'start'} to {data['to'] or 'now'}"]);w.writerow([])
    w.writerow(['Month','Agreement','Vehicle']+[LABELS[f] for f in fields]+['Bill state'])
    for r in data['rows']:w.writerow([r['month'],r['ref'],excel_safe(r['vehicle'])]+[str(r[f]) for f in fields]+[r['bill_state']])
    t=data['totals'];w.writerow(['Total','','']+[str(t[f]) for f in fields]+[t['bill_state']])
    return '﻿'+buf.getvalue()
