from datetime import date
from django.db.models import Prefetch
from ninja import Query, Router
from ..i18n import has_code, translate, word
from ninja.errors import HttpError
from .errors import ApiError
from .. import policies, services
from ..models import Agreement, Allocation, Expense, Obligation, Period, NotificationOutbox
from ._shared import membership, result

router = Router()

def person(user):
    """Who recorded an entry, as shown in the cashbook."""
    return (user.get_full_name() or user.username) if user else ''


NOTICE_STATUS={'queued':'Queued','sent':'Sent','delivered':'Delivered','failed':'Failed','skipped_no_contact':'Skipped'}
NOTICE_EVENTS=('rent_issued','expense_posted','payment_recorded','settlement_closed')

def notice_text(n):
    """A readable line for the notification list instead of the raw payload."""
    if n.event_type in NOTICE_EVENTS:
        return translate('notice_detail_'+n.event_type,{k:v for k,v in n.payload.items()})
    return ', '.join(f'{k.replace("_"," ")} {v}' for k,v in n.payload.items()) or notice_title(n.event_type)

def notice_title(event_type):
    """The short heading of a message, in the user's language."""
    code='notice_title_'+event_type
    return translate(code) if has_code(code) else event_type.replace('_',' ').capitalize()

@router.get('/orgs/{org_id}/snapshot')
def snapshot(request,org_id:int,month:date|None=None,from_:date|None=Query(None,alias='from'),to:date|None=None,branch_id:int|None=None):
    """One month (`month`, unchanged shape) or a range: every rent period overlapping [from, to], not prorated. With a range, entries cover all those periods,
    `periodIds`/`obligationIds` point at each agreement's latest period in the range, and `backend.periods` lists them all."""
    m=membership(request,org_id,'snapshot.read')
    start,end=result(services.resolve_range,from_,to)
    if not month and not (start or end):raise ApiError(422, 'give_a_month_or_from_and_to')
    ranged=month is None
    qs=policies.branch_scope(m,Agreement.objects.filter(organization_id=org_id,active=True),branch_id).select_related('vehicle__owner','customer','driver').order_by('pk')[:200]
    agreement_list=list(qs)
    # everything the loop below needs is fetched in a handful of queries, however many agreements there are (it used to be several per agreement)
    period_qs=Period.objects.filter(agreement_id__in=[a.pk for a in agreement_list])
    period_qs=services.overlapping_periods(period_qs,start,end) if ranged else period_qs.filter(month=month)
    periods_by={}
    for x in period_qs.order_by('month','pk'):periods_by.setdefault(x.agreement_id,[]).append(x)
    period_pks=[x.pk for xs in periods_by.values() for x in xs]
    expenses_by={}
    for e in Expense.objects.filter(period_id__in=period_pks).select_related('recorded_by').order_by('pk'):expenses_by.setdefault(e.period_id,[]).append(e)
    obligations_by={}
    for ob in Obligation.objects.filter(period_id__in=period_pks).order_by('pk').prefetch_related(Prefetch('allocations',queryset=Allocation.objects.select_related('payment__party','payment__recorded_by').order_by('pk'))):obligations_by.setdefault(ob.period_id,[]).append(ob)
    agreements=[];entries=[];closed=[];period_ids={};obligation_ids={};expense_ids={};all_periods={}
    for a in agreement_list:
        agreement_id=f'AGR-{a.pk}'
        agreements.append({'id':agreement_id,'vehicle':a.vehicle.model_name,'registration':a.vehicle.registration,'owner':a.vehicle.owner.name,'customer':a.customer.name,'driver':a.driver.name if a.driver_id and a.driver_provider!='none' else None,'rent':float(a.customer_rent),'ownerRent':float(a.owner_rent),'salary':float(a.driver_salary),'start':a.start_date.isoformat(),'end':a.end_date.isoformat() if a.end_date else None,'status':'Active','ownerId':a.vehicle.owner_id,'customerId':a.customer_id,'driverId':a.driver_id if a.driver_provider!='none' else None,'rules':{'fuel':a.fuel_responsible.title(),'repair':a.repair_responsible.title(),'offset':a.owner_offset_allowed}})
        found=periods_by.get(a.pk,[])
        if not found:continue
        p=found[-1]
        period_ids[agreement_id]=p.pk
        all_periods[agreement_id]=[{'id':x.pk,'month':x.month.isoformat(),'closed':bool(x.closed_at)} for x in found]
        if p.closed_at:closed.append(agreement_id)
        obligation_ids[agreement_id]={o.kind.split('_')[0]:o.pk for o in obligations_by.get(p.pk,[])}
        for q in found:
            for e in expenses_by.get(q.pk,[]):
                public_id=f'EXP-{e.pk}'
                expense_ids[public_id]=e.pk
                entries.append({'id':public_id,'agreementId':agreement_id,'type':'Expense','amount':float(e.amount),'date':e.event_date.isoformat(),'category':e.category.title(),'paidBy':e.paid_by.title(),'responsible':e.responsible.title(),'reference':e.reference,'description':e.description or word('category',e.category),'status':{'draft':'Draft','pending':'Pending approval','posted':'Posted','reversed':'Reversed'}[e.state],'offset':e.offset_owner,'recordedBy':person(e.recorded_by)})
            for alloc in obligations_by.get(q.pk,[]):
                for row in alloc.allocations.all():
                    pay=row.payment
                    entries.append({'id':f'PAY-{pay.pk}-{row.pk}','agreementId':agreement_id,'type':{'customer_payment':'Customer payment','owner_payout':'Owner payout','driver_payout':'Driver payout','salary_advance':'Salary advance'}[pay.kind],'amount':float(row.amount),'date':pay.event_date.isoformat(),'category':'Rent' if pay.kind=='customer_payment' else 'Payout','paidBy':pay.party.name if pay.kind=='customer_payment' else 'Operator','responsible':'Operator','reference':pay.reference,'description':pay.note or word('entrytype',pay.kind),'recordedBy':person(pay.recorded_by),'status':'Posted'})
    notices=[{'id':f'NT-{n.pk}','title':notice_title(n.event_type),'detail':notice_text(n),'date':n.created_at.date().isoformat(),'status':NOTICE_STATUS.get(n.state,n.state.title()),'read':False,'recipient':n.recipient.name,'error':n.last_error,'deliveries':[{'channel':d.channel,'isAdminCopy':d.is_admin_copy,'to':d.to_address,'state':d.state,'error':d.last_error} for d in n.deliveries.all()]} for n in policies.branch_scope(m,NotificationOutbox.objects.filter(organization_id=org_id),branch_id).select_related('recipient').prefetch_related('deliveries').order_by('-id')[:200]]
    return {'agreements':agreements,'entries':entries,'notices':notices,'closed':closed,'backend':{'orgId':org_id,'periodIds':period_ids,'obligationIds':obligation_ids,'expenseIds':expense_ids,**({'periods':all_periods} if ranged else {})}}
