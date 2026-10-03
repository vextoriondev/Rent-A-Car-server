from datetime import date
from ninja import Router
from ninja.errors import HttpError
from .. import services
from ..models import Account, Charge, Invoice, Period
from ._shared import ChargeIn, InvoiceIn, ReasonIn, membership, result, charge_dict, invoice_dict, money_dict

router = Router()

@router.get('/orgs/{org_id}/accounts')
def accounts(request,org_id:int):
    m=membership(request,org_id,'ledger.read')
    services.ensure_accounts(m.organization)
    return [{'code':a.code,'name':a.name,'type':a.type,'subledger_kind':a.subledger_kind} for a in Account.objects.filter(organization=m.organization).order_by('code')]

@router.post('/orgs/{org_id}/charges')
def post_charge(request,org_id:int,payload:ChargeIn):
    m=membership(request,org_id,'charge.post')
    return charge_dict(result(services.post_charge,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key','')))

@router.get('/orgs/{org_id}/charges')
def charges(request,org_id:int,period_id:int|None=None):
    m=membership(request,org_id,'read_financial')
    qs=Charge.objects.filter(organization=m.organization)
    if period_id:qs=qs.filter(period_id=period_id)
    return [charge_dict(c) for c in qs.order_by('-id')[:200]]

@router.post('/orgs/{org_id}/periods/{period_id}/carry-forward')
def carry_forward(request,org_id:int,period_id:int):
    m=membership(request,org_id,'charge.post')
    return [charge_dict(c) for c in result(services.carry_forward,m.organization,request.user,period_id)]

@router.get('/orgs/{org_id}/periods/{period_id}/ledger')
def ledger(request,org_id:int,period_id:int):
    m=membership(request,org_id,'ledger.read')
    p=Period.objects.filter(pk=period_id,organization=m.organization).first()
    if not p:raise HttpError(404,'Period not found')
    from ..models import JournalLine
    lines=JournalLine.objects.filter(period=p).select_related('entry').order_by('entry_id','id')
    stmt=services.ledger_statement(p)
    return {'statement':money_dict(stmt),'reconciles':not services.diff_statements(stmt,services.obligation_statement(p)),
        'lines':[{'entry_id':l.entry_id,'source_type':l.entry.source_type,'source_id':l.entry.source_id,'account':l.account,'debit':str(l.debit),'credit':str(l.credit),'party_id':l.party_id} for l in lines]}

@router.get('/orgs/{org_id}/periods/{period_id}/invoice-preview')
def invoice_preview(request,org_id:int,period_id:int):
    m=membership(request,org_id,'invoice.issue')
    return money_dict(result(services.preview_invoice,m.organization,period_id))

@router.post('/orgs/{org_id}/invoices')
def issue_invoice(request,org_id:int,payload:InvoiceIn):
    m=membership(request,org_id,'invoice.issue')
    i=result(services.issue_invoice,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''))
    return invoice_dict(i,services.invoice_summary(i))

@router.get('/orgs/{org_id}/invoices')
def invoices(request,org_id:int,period_id:int|None=None):
    m=membership(request,org_id,'read_financial')
    qs=Invoice.objects.filter(organization=m.organization).prefetch_related('lines')
    if period_id:qs=qs.filter(period_id=period_id)
    return [invoice_dict(i,services.invoice_summary(i)) for i in qs.order_by('-id')[:200]]

@router.get('/orgs/{org_id}/invoices/{invoice_id}')
def invoice(request,org_id:int,invoice_id:int):
    m=membership(request,org_id,'read_financial')
    i=Invoice.objects.filter(pk=invoice_id,organization=m.organization).prefetch_related('lines').first()
    if not i:raise HttpError(404,'Invoice not found')
    return invoice_dict(i,services.invoice_summary(i))

@router.post('/orgs/{org_id}/invoices/{invoice_id}/void')
def void_invoice(request,org_id:int,invoice_id:int,payload:ReasonIn):
    m=membership(request,org_id,'invoice.issue')
    i=result(services.void_invoice,m.organization,request.user,invoice_id,payload.reason)
    return invoice_dict(i,services.invoice_summary(i))
