from datetime import date
from ninja import Router
from ..models import Agreement, Period, NotificationOutbox
from ._shared import membership

router = Router()

@router.get('/orgs/{org_id}/snapshot')
def snapshot(request,org_id:int,month:date):
    membership(request,org_id,'snapshot.read')
    qs=Agreement.objects.filter(organization_id=org_id,active=True).select_related('vehicle__owner','customer','driver').order_by('pk')[:200]
    agreements=[];entries=[];closed=[];period_ids={};obligation_ids={};expense_ids={}
    for a in qs:
        agreement_id=f'AGR-{a.pk}'
        agreements.append({'id':agreement_id,'vehicle':a.vehicle.model_name,'registration':a.vehicle.registration,'owner':a.vehicle.owner.name,'customer':a.customer.name,'driver':a.driver.name,'rent':float(a.customer_rent),'ownerRent':float(a.owner_rent),'salary':float(a.driver_salary),'start':a.start_date.isoformat(),'status':'Active','rules':{'fuel':a.fuel_responsible.title(),'repair':a.repair_responsible.title(),'offset':a.owner_offset_allowed}})
        p=Period.objects.filter(agreement=a,month=month).first()
        if not p:continue
        period_ids[agreement_id]=p.pk
        if p.closed_at:closed.append(agreement_id)
        obligation_ids[agreement_id]={o.kind.split('_')[0]:o.pk for o in p.obligations.all()}
        for e in p.expenses.all():
            public_id=f'EXP-{e.pk}'
            expense_ids[public_id]=e.pk
            entries.append({'id':public_id,'agreementId':agreement_id,'type':'Expense','amount':float(e.amount),'date':e.event_date.isoformat(),'category':e.category.title(),'paidBy':e.paid_by.title(),'responsible':e.responsible.title(),'reference':e.reference,'description':e.description or e.category.title(),'status':{'draft':'Draft','pending':'Pending approval','posted':'Posted','reversed':'Reversed'}[e.state],'offset':e.offset_owner})
        for alloc in p.obligations.all():
            for row in alloc.allocations.select_related('payment').all():
                pay=row.payment
                entries.append({'id':f'PAY-{pay.pk}-{row.pk}','agreementId':agreement_id,'type':{'customer_payment':'Customer payment','owner_payout':'Owner payout','driver_payout':'Driver payout','salary_advance':'Salary advance'}[pay.kind],'amount':float(row.amount),'date':pay.event_date.isoformat(),'category':'Rent' if pay.kind=='customer_payment' else 'Payout','paidBy':pay.party.name if pay.kind=='customer_payment' else 'Operator','responsible':'Operator','reference':pay.reference,'description':pay.get_kind_display(),'status':'Posted'})
    notices=[{'id':f'NT-{n.pk}','title':n.event_type.replace('_',' ').title(),'detail':str(n.payload),'date':n.created_at.date().isoformat(),'status':n.state.title(),'read':False,'recipient':n.recipient.name} for n in NotificationOutbox.objects.filter(organization_id=org_id).select_related('recipient').order_by('-id')[:200]]
    return {'agreements':agreements,'entries':entries,'notices':notices,'closed':closed,'backend':{'orgId':org_id,'periodIds':period_ids,'obligationIds':obligation_ids,'expenseIds':expense_ids}}
