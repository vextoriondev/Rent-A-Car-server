from ninja import Router
from ..models import Expense
from .. import services
from ._shared import ExpenseIn, ReasonIn, membership, result, expense_dict

router = Router()

@router.post('/orgs/{org_id}/expenses')
def record_expense(request,org_id:int,payload:ExpenseIn):
    m=membership(request,org_id,('admin','accountant','operations'))
    e=result(services.record_expense,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''))
    return expense_dict(e)

@router.get('/orgs/{org_id}/expenses')
def expenses(request,org_id:int,period_id:int|None=None):
    membership(request,org_id)
    qs=Expense.objects.filter(period__organization_id=org_id)
    if period_id:qs=qs.filter(period_id=period_id)
    return [expense_dict(e) for e in qs.order_by('-id')[:200]]

@router.post('/orgs/{org_id}/expenses/{expense_id}/approve')
def approve_expense(request,org_id:int,expense_id:int):
    membership(request,org_id,('admin','accountant'))
    return expense_dict(result(services.approve_expense,membership(request,org_id).organization,request.user,expense_id))

@router.post('/orgs/{org_id}/expenses/{expense_id}/reverse')
def reverse_expense(request,org_id:int,expense_id:int,payload:ReasonIn):
    membership(request,org_id,('admin','accountant'))
    return expense_dict(result(services.reverse_expense,membership(request,org_id).organization,request.user,expense_id,payload.reason))
