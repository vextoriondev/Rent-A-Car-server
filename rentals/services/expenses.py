from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from ..models import Obligation, Expense, OwnerOffset, JournalEntry
from .helpers import ZERO, DomainError, bd, replay, remember, journal, notice, locked_period
from .statements import statement

def post_expense_locked(expense,approver=None):
    p=expense.period
    if p.closed_at:raise DomainError('Period is closed')
    if expense.state==Expense.State.POSTED:return expense
    if expense.state==Expense.State.REVERSED:raise DomainError('Cannot post a reversed expense')
    if expense.offset_owner:
        owner_ob=Obligation.objects.select_for_update().get(period=p,kind=Obligation.Kind.OWNER)
        current=statement(p)['owner']['due']
        if expense.amount>current:raise DomainError('Offset exceeds unpaid owner balance')
        OwnerOffset.objects.create(expense=expense,obligation=owner_ob,amount=expense.amount)
    expense.state=Expense.State.POSTED
    expense.approved_by=approver
    expense.save(update_fields=['state','approved_by'])
    if expense.paid_by=='operator':
        debit_account={'operator':'OPERATING_EXPENSE','owner':'OWNER_RECOVERABLE','customer':'CUSTOMER_RECOVERABLE'}[expense.responsible]
        lines=[(debit_account,expense.amount,ZERO),('CASH',ZERO,expense.amount)]
        if expense.offset_owner:lines += [('OWNER_PAYABLE',expense.amount,ZERO),('OWNER_RECOVERABLE',ZERO,expense.amount)]
        journal(p.organization,'expense',expense.pk,lines)
    notice(p.organization,f'expense:{expense.pk}:owner',p.agreement.vehicle.owner,'expense_posted',{'expense_id':expense.public_id}) if expense.responsible=='owner' else None
    return expense

@transaction.atomic
def record_expense(org,user,data,key):
    existing=replay(org,'expense',key,data,Expense)
    if existing:return existing
    p=locked_period(org,data['period_id'])
    amount=bd(data['amount'])
    category=data['category'].lower()
    rule={'fuel':p.agreement.fuel_responsible,'repair':p.agreement.repair_responsible}.get(category)
    if category not in ('fuel','repair','toll','maintenance','cleaning','other'):raise DomainError('Unsupported expense category')
    liable=data['responsible']
    if liable not in ('owner','operator','customer'):raise DomainError('Unsupported responsible party')
    if rule and liable!=rule:raise DomainError('Agreement expense rule differs; create an approved revision first')
    paid_by=data['paid_by']
    if paid_by not in ('operator','owner'):raise DomainError('This slice supports operator or owner funded expenses')
    if paid_by=='owner' and liable!='owner':raise DomainError('Cross-party owner-funded expenses need a reimbursement workflow')
    offset=bool(data.get('offset_owner'))
    if offset and not (liable=='owner' and paid_by=='operator' and p.agreement.owner_offset_allowed):raise DomainError('Owner offset is not authorized')
    if not data['reference'].strip():raise DomainError('Reference is required')
    expense=Expense.objects.create(period=p,amount=amount,event_date=data.get('event_date',p.month),category=category,paid_by=paid_by,responsible=liable,offset_owner=offset,reference=data['reference'].strip(),description=data.get('description',''),recorded_by=user,rule_snapshot={'fuel_responsible':p.agreement.fuel_responsible,'repair_responsible':p.agreement.repair_responsible,'owner_offset_allowed':p.agreement.owner_offset_allowed})
    if amount<Decimal('5000.00'):post_expense_locked(expense)
    remember(org,'expense',key,data,expense)
    return expense

@transaction.atomic
def approve_expense(org,user,expense_id):
    expense=Expense.objects.select_for_update().select_related('period','period__agreement','period__agreement__vehicle__owner').get(pk=expense_id,period__organization=org)
    locked_period(org,expense.period_id)
    return post_expense_locked(expense,user)

@transaction.atomic
def reverse_expense(org,user,expense_id,reason):
    if not reason.strip():raise DomainError('Reversal reason is required')
    expense=Expense.objects.select_for_update().select_related('period').get(pk=expense_id,period__organization=org)
    locked_period(org,expense.period_id)
    if expense.state!=Expense.State.POSTED:raise DomainError('Only posted expenses can be reversed')
    original=JournalEntry.objects.filter(organization=org,source_type='expense',source_id=str(expense.pk)).first()
    if original:journal(org,'expense_reversal',expense.pk,[(line.account,line.credit,line.debit) for line in original.lines.all()])
    expense.state=Expense.State.REVERSED
    expense.reversed_by=user
    expense.reversed_at=timezone.now()
    expense.reversal_reason=reason.strip()
    expense.save(update_fields=['state','reversed_by','reversed_at','reversal_reason'])
    return expense
