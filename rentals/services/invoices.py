"""Customer invoices. An invoice groups customer-side obligations; it posts nothing (receivables are already in the ledger).
Only issued/void is stored. paid / partially_paid / overdue are derived from the allocations on the invoiced obligations."""
from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from ..models import Allocation, Invoice, InvoiceLine, InvoiceSequence, Obligation, Period
from .helpers import ZERO, DomainError, replay, remember, total

def _billable(org,period):
    """Customer-side obligations of the period that are not already on a live invoice (carry_out lines are never invoiced)."""
    taken=InvoiceLine.objects.filter(invoice__state=Invoice.State.ISSUED).values('obligation_id')
    return Obligation.objects.filter(period=period,party__kind='customer').exclude(kind='carry_out').exclude(pk__in=taken).select_related('party').order_by('pk')

def preview_invoice(org,period_id):
    period=Period.objects.select_related('agreement__customer').get(pk=period_id,organization=org)
    lines=[{'obligation_id':o.pk,'kind':o.kind,'description':o.get_kind_display(),'amount':o.amount} for o in _billable(org,period)]
    return {'period_id':period.pk,'party_id':period.agreement.customer_id,'lines':lines,'total':sum((l['amount'] for l in lines),ZERO)}

def invoice_summary(invoice,today=None):
    today=today or date.today()
    cents=Decimal('0.01')
    amount=total(invoice.lines,'amount').quantize(cents)
    paid=total(Allocation.objects.filter(obligation__in=invoice.lines.values('obligation_id'))).quantize(cents)
    outstanding=amount-paid
    if invoice.state==Invoice.State.VOID:status='void'
    elif outstanding<=0:status='paid'
    elif invoice.due_date<today:status='overdue'
    elif paid>0:status='partially_paid'
    else:status='issued'
    return {'amount':amount,'paid':paid,'outstanding':outstanding,'status':status}

@transaction.atomic
def issue_invoice(org,user,data,key):
    """Issue one invoice for a period's customer obligations (all open ones, or the given obligation_ids). Idempotent per key."""
    existing=replay(org,'invoice',key,data,Invoice)
    if existing:return existing
    period=Period.objects.select_for_update().select_related('agreement__customer').get(pk=data['period_id'],organization=org)
    available={o.pk:o for o in _billable(org,period).select_for_update()}
    wanted=data.get('obligation_ids') or list(available)
    if not wanted:raise DomainError('Nothing to invoice for this period')
    if len(set(wanted))!=len(wanted) or any(i not in available for i in wanted):raise DomainError('An obligation is unavailable, already invoiced, or not a customer charge')
    amount=sum((available[i].amount for i in wanted),ZERO)
    if amount<=0:raise DomainError('Invoice total must be positive')
    seq,_=InvoiceSequence.objects.select_for_update().get_or_create(organization=org)
    seq.last_number+=1;seq.save(update_fields=['last_number'])
    issue=data.get('issue_date') or date.today()
    invoice=Invoice.objects.create(organization=org,number=f'INV-{issue.year}-{seq.last_number:05d}',party=period.agreement.customer,agreement=period.agreement,period=period,issue_date=issue,due_date=data.get('due_date') or issue+timedelta(days=15),created_by=user)
    if invoice.due_date<invoice.issue_date:raise DomainError('Due date precedes issue date')
    InvoiceLine.objects.bulk_create([InvoiceLine(invoice=invoice,obligation=available[i],description=available[i].get_kind_display(),amount=available[i].amount) for i in wanted])
    remember(org,'invoice',key,data,invoice)
    return invoice

@transaction.atomic
def void_invoice(org,user,invoice_id,reason):
    invoice=Invoice.objects.select_for_update().get(pk=invoice_id,organization=org)
    if not reason.strip():raise DomainError('A reason is required')
    if invoice.state==Invoice.State.VOID:return invoice
    if invoice_summary(invoice)['paid']>0:raise DomainError('Payments are allocated to this invoice; reverse them before voiding')
    invoice.state=Invoice.State.VOID;invoice.void_reason=reason.strip()[:240];invoice.save(update_fields=['state','void_reason'])
    return invoice
