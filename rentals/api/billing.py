from datetime import date
from decimal import Decimal
from ninja import Router
from ninja.errors import HttpError
from .. import policies, services
from ..models import Account, Allocation, Charge, Invoice, Obligation, Period
from ._shared import ChargeIn, InvoiceIn, ReasonIn, membership, result, charge_dict, invoice_dict, money_dict, paginate

router = Router()

@router.get('/orgs/{org_id}/accounts')
def accounts(request,org_id:int):
    m=membership(request,org_id,'ledger.read')
    services.ensure_accounts(m.organization)
    return [{'code':a.code,'name':a.name,'type':a.type,'subledger_kind':a.subledger_kind} for a in Account.objects.filter(organization=m.organization).order_by('code')]

@router.post('/orgs/{org_id}/charges')
def post_charge(request,org_id:int,payload:ChargeIn):
    m=membership(request,org_id,'charge.post',period=payload.period_id)
    return charge_dict(result(services.post_charge,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''),can_approve=m.role=='admin'))

@router.get('/orgs/{org_id}/periods/{period_id}/charge-suggestion')
def charge_suggestion(request,org_id:int,period_id:int,kind:str,quantity:Decimal=Decimal('1')):
    m=membership(request,org_id,'charge.post',period=period_id)
    p=Period.objects.select_related('agreement','agreement_version').filter(pk=period_id,organization=m.organization).first()
    if not p:raise HttpError(404,'Period not found')
    return money_dict(result(services.charge_suggestion,p,kind,quantity))

@router.get('/orgs/{org_id}/charges')
def charges(request,org_id:int,period_id:int|None=None,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read_financial')
    qs=policies.branch_scope(m,Charge.objects.filter(organization=m.organization),branch_id)
    if period_id:qs=qs.filter(period_id=period_id)
    return paginate(qs.order_by('-id'),limit,offset,lambda c:charge_dict(c))

@router.post('/orgs/{org_id}/periods/{period_id}/carry-forward')
def carry_forward(request,org_id:int,period_id:int):
    m=membership(request,org_id,'charge.post',period=period_id)
    return [charge_dict(c) for c in result(services.carry_forward,m.organization,request.user,period_id)]

@router.get('/orgs/{org_id}/periods/{period_id}/ledger')
def ledger(request,org_id:int,period_id:int):
    m=membership(request,org_id,'ledger.read',period=period_id)
    p=Period.objects.filter(pk=period_id,organization=m.organization).first()
    if not p:raise HttpError(404,'Period not found')
    from ..models import JournalLine
    lines=JournalLine.objects.filter(period=p).select_related('entry').order_by('entry_id','id')
    stmt=services.ledger_statement(p)
    return {'statement':money_dict(stmt),'reconciles':not services.diff_statements(stmt,services.obligation_statement(p)),
        'lines':[{'entry_id':l.entry_id,'source_type':l.entry.source_type,'source_id':l.entry.source_id,'account':l.account,'debit':str(l.debit),'credit':str(l.credit),'party_id':l.party_id} for l in lines]}

@router.get('/orgs/{org_id}/periods/{period_id}/obligations')
def obligations(request,org_id:int,period_id:int,side:str='customer'):
    """Positive obligations on one side of a period with what is still open on each (amount minus allocated), for payment splitting. Owner offsets are not subtracted here; the payment endpoint still enforces them."""
    m=membership(request,org_id,'read_financial',period=period_id)
    if side not in ('customer','owner','driver'):raise HttpError(422,'Unknown side')
    p=Period.objects.filter(pk=period_id,organization=m.organization).first()
    if not p:raise HttpError(404,'Period not found')
    rows=[];notes={c.obligation_id:c.description for c in Charge.objects.filter(period=p).exclude(obligation=None)}
    for o in Obligation.objects.filter(period=p,party__kind=side).exclude(kind='carry_out').order_by('pk'):
        paid=services.total(Allocation.objects.filter(obligation=o))
        if o.amount>0:rows.append({'obligation_id':o.pk,'kind':o.kind,'description':o.get_kind_display()+(f': {notes[o.pk]}' if notes.get(o.pk) else ''),'amount':str(o.amount),'allocated':str(paid),'open_amount':str(max(o.amount-paid,0))})
    return rows

@router.get('/orgs/{org_id}/periods/{period_id}/invoice-preview')
def invoice_preview(request,org_id:int,period_id:int):
    m=membership(request,org_id,'invoice.issue',period=period_id)
    return money_dict(result(services.preview_invoice,m.organization,period_id))

@router.post('/orgs/{org_id}/invoices')
def issue_invoice(request,org_id:int,payload:InvoiceIn):
    m=membership(request,org_id,'invoice.issue',period=payload.period_id,obligations=payload.obligation_ids or [])
    i=result(services.issue_invoice,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''))
    return invoice_dict(i,services.invoice_summary(i))

@router.get('/orgs/{org_id}/invoices')
def invoices(request,org_id:int,period_id:int|None=None,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read_financial')
    qs=policies.branch_scope(m,Invoice.objects.filter(organization=m.organization),branch_id).prefetch_related('lines')
    if period_id:qs=qs.filter(period_id=period_id)
    return paginate(qs.order_by('-id'),limit,offset,lambda i:invoice_dict(i,services.invoice_summary(i)))

@router.get('/orgs/{org_id}/invoices/{invoice_id}')
def invoice(request,org_id:int,invoice_id:int):
    m=membership(request,org_id,'read_financial',invoice=invoice_id)
    i=Invoice.objects.filter(pk=invoice_id,organization=m.organization).prefetch_related('lines').first()
    if not i:raise HttpError(404,'Invoice not found')
    return invoice_dict(i,services.invoice_summary(i))

@router.post('/orgs/{org_id}/invoices/{invoice_id}/void')
def void_invoice(request,org_id:int,invoice_id:int,payload:ReasonIn):
    m=membership(request,org_id,'invoice.issue',invoice=invoice_id)
    i=result(services.void_invoice,m.organization,request.user,invoice_id,payload.reason)
    return invoice_dict(i,services.invoice_summary(i))
