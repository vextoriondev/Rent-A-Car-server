from ninja import Router
from ninja.errors import HttpError
from .. import policies, services
from ..models import Expense, ExpenseCategory, ExpenseRule
from ._shared import ExpenseIn, ExpensePatch, CategoryIn, RuleIn, ReasonIn, membership, result, expense_dict, rule_dict

router = Router()

@router.post('/orgs/{org_id}/expenses')
def record_expense(request,org_id:int,payload:ExpenseIn):
    m=membership(request,org_id,'expense.record')
    e=result(services.record_expense,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''),policies.can(m.role,'expense.override'))
    return expense_dict(e)

@router.get('/orgs/{org_id}/expenses')
def expenses(request,org_id:int,period_id:int|None=None):
    membership(request,org_id,'read_financial')
    qs=Expense.objects.filter(period__organization_id=org_id)
    if period_id:qs=qs.filter(period_id=period_id)
    return [expense_dict(e) for e in qs.order_by('-id')[:200]]

@router.post('/orgs/{org_id}/expenses/{expense_id}/approve')
def approve_expense(request,org_id:int,expense_id:int):
    m=membership(request,org_id,'expense.approve')
    return expense_dict(result(services.approve_expense,m.organization,request.user,expense_id))

@router.post('/orgs/{org_id}/expenses/{expense_id}/reverse')
def reverse_expense(request,org_id:int,expense_id:int,payload:ReasonIn):
    m=membership(request,org_id,'expense.reverse')
    return expense_dict(result(services.reverse_expense,m.organization,request.user,expense_id,payload.reason))

@router.patch('/orgs/{org_id}/expenses/{expense_id}')
def update_draft(request,org_id:int,expense_id:int,payload:ExpensePatch):
    m=membership(request,org_id,'expense.record')
    return expense_dict(result(services.update_draft,m.organization,request.user,expense_id,payload.dict(exclude_none=True),policies.can(m.role,'expense.override')))

@router.post('/orgs/{org_id}/expenses/{expense_id}/submit')
def submit_expense(request,org_id:int,expense_id:int):
    m=membership(request,org_id,'expense.record')
    return expense_dict(result(services.submit_expense,m.organization,request.user,expense_id))

@router.delete('/orgs/{org_id}/expenses/{expense_id}')
def discard_draft(request,org_id:int,expense_id:int):
    m=membership(request,org_id,'expense.record')
    result(services.discard_draft,m.organization,request.user,expense_id)
    return {'discarded':True}

@router.get('/orgs/{org_id}/expense-categories')
def categories(request,org_id:int):
    m=membership(request,org_id,'read_financial')
    services.ensure_categories(m.organization)
    return [{'code':c.code,'name':c.name,'active':c.active} for c in ExpenseCategory.objects.filter(organization=m.organization).order_by('name')]

@router.post('/orgs/{org_id}/expense-categories')
def create_category(request,org_id:int,payload:CategoryIn):
    m=membership(request,org_id,'category.write')
    c=result(services.create_category,m.organization,payload.code,payload.name)
    return {'code':c.code,'name':c.name,'active':c.active}

@router.put('/orgs/{org_id}/agreement-versions/{version_id}/rules/{category}')
def set_rule(request,org_id:int,version_id:int,category:str,payload:RuleIn):
    m=membership(request,org_id,'rules.write')
    return rule_dict(result(services.set_expense_rule,m.organization,request.user,version_id,category,**payload.dict()))

@router.delete('/orgs/{org_id}/agreement-versions/{version_id}/rules/{category}')
def delete_rule(request,org_id:int,version_id:int,category:str):
    m=membership(request,org_id,'rules.write')
    result(services.delete_expense_rule,m.organization,request.user,version_id,category)
    return {'deleted':True}
