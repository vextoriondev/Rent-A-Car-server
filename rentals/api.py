"""Versioned, session-authenticated API. Money is serialized as decimal strings."""
from datetime import date
from decimal import Decimal
from typing import Literal
from django.contrib.auth import authenticate, get_user_model, login as auth_login, logout as auth_logout
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.security import django_auth
from ninja.utils import check_csrf
from .models import Organization, Membership, Party, Vehicle, Agreement, Period, Obligation, Expense, Payment, NotificationOutbox
from . import services

api=NinjaAPI(auth=django_auth,title='RentFlow API',version='1.0.0')

class PartyIn(Schema): kind:Literal['owner','driver','customer']; name:str; phone:str=''; email:str=''
class VehicleIn(Schema): registration:str; model_name:str; owner_id:int
class AgreementIn(Schema):
    vehicle_id:int; customer_id:int; driver_id:int; start_date:date; end_date:date|None=None
    customer_rent:Decimal; owner_rent:Decimal; driver_salary:Decimal
    fuel_responsible:Literal['owner','operator','customer']='owner'
    repair_responsible:Literal['owner','operator','customer']='owner'
    owner_offset_allowed:bool=False
class ExpenseIn(Schema):
    period_id:int; category:str; amount:Decimal; event_date:date; paid_by:Literal['operator','owner']; responsible:Literal['owner','operator','customer']; offset_owner:bool=False; reference:str; description:str=''
class AllocationIn(Schema): obligation_id:int; amount:Decimal
class PaymentIn(Schema):
    kind:Literal['customer_payment','owner_payout','driver_payout','salary_advance']
    amount:Decimal; reference:str; method:str='bank'; event_date:date; allocations:list[AllocationIn]
class ReasonIn(Schema): reason:str
class LoginIn(Schema): username:str; password:str
class RegisterIn(Schema): organization_name:str; username:str; email:str=''; password:str
class QuickAgreementIn(Schema):
    customer:str; owner:str; driver:str; vehicle:str; registration:str; start:date
    rent:Decimal; owner_rent:Decimal; salary:Decimal
    fuel:Literal['Owner','Operator','Customer']='Owner'
    repair:Literal['Owner','Operator','Customer']='Owner'
    offset:bool=False

def membership(request:HttpRequest,org_id:int,write:tuple[str,...]|None=None):
    member=Membership.objects.filter(organization_id=org_id,user=request.user).select_related('organization').first()
    if not member:raise HttpError(404,'Organization not found')
    if write and member.role not in write:raise HttpError(403,'Your role cannot perform this action')
    return member

def result(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except services.DomainError as exc:raise HttpError(422,str(exc)) from exc
    except (Period.DoesNotExist,Agreement.DoesNotExist,Expense.DoesNotExist,Party.DoesNotExist,Vehicle.DoesNotExist,Obligation.DoesNotExist):raise HttpError(404,'Record not found')
    except IntegrityError as exc:raise HttpError(409,'Duplicate or conflicting record') from exc

def agreement_dict(a):
    return {'id':a.pk,'vehicle':{'id':a.vehicle_id,'model_name':a.vehicle.model_name,'registration':a.vehicle.registration},'owner':{'id':a.vehicle.owner_id,'name':a.vehicle.owner.name},'customer':{'id':a.customer_id,'name':a.customer.name},'driver':{'id':a.driver_id,'name':a.driver.name},'start_date':a.start_date.isoformat(),'end_date':a.end_date.isoformat() if a.end_date else None,'customer_rent':str(a.customer_rent),'owner_rent':str(a.owner_rent),'driver_salary':str(a.driver_salary),'fuel_responsible':a.fuel_responsible,'repair_responsible':a.repair_responsible,'owner_offset_allowed':a.owner_offset_allowed}

def money_dict(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,dict):return {k:money_dict(v) for k,v in value.items()}
    return value

def expense_dict(e):
    return {'id':e.pk,'public_id':e.public_id,'period_id':e.period_id,'amount':str(e.amount),'category':e.category,'paid_by':e.paid_by,'responsible':e.responsible,'offset_owner':e.offset_owner,'reference':e.reference,'state':e.state,'rule_snapshot':e.rule_snapshot,'reversal_reason':e.reversal_reason}

def payment_dict(p):
    return {'id':p.pk,'public_id':p.public_id,'kind':p.kind,'party_id':p.party_id,'amount':str(p.amount),'reference':p.reference,'allocations':[{'obligation_id':x.obligation_id,'amount':str(x.amount)} for x in p.allocations.all()]}

@api.get('/me')
def me(request):
    return {'user_id':request.user.pk,'username':request.user.get_username(),'organizations':[{'id':m.organization_id,'name':m.organization.name,'role':m.role} for m in Membership.objects.filter(user=request.user).select_related('organization')]}

@api.get('/csrf',auth=None)
@ensure_csrf_cookie
@csrf_exempt
def csrf_cookie(request):
    return HttpResponse(status=204)

def require_csrf(request:HttpRequest):
    forbidden=check_csrf(request)
    if forbidden:raise HttpError(403,'Session security check failed. Reload the page and try again.')

@api.post('/auth/login',auth=None)
def login_view(request,payload:LoginIn):
    require_csrf(request)
    user=authenticate(request,username=payload.username.strip(),password=payload.password)
    if not user:raise HttpError(401,'Incorrect username or password.')
    auth_login(request,user)
    return {'user_id':user.pk,'username':user.get_username()}

@api.post('/auth/logout',auth=None)
def logout_view(request):
    require_csrf(request)
    auth_logout(request)
    return HttpResponse(status=204)

@api.post('/auth/register',auth=None)
def register_view(request,payload:RegisterIn):
    require_csrf(request)
    org_name=payload.organization_name.strip()
    username=payload.username.strip()
    if not org_name:raise HttpError(422,'Company or workspace name is required.')
    if not username:raise HttpError(422,'Username is required.')
    if len(payload.password)<8:raise HttpError(422,'Password must be at least 8 characters.')
    User=get_user_model()
    if User.objects.filter(username__iexact=username).exists():raise HttpError(409,'That username is already taken.')
    with transaction.atomic():
        user=User.objects.create_user(username=username,email=payload.email.strip(),password=payload.password)
        org=Organization.objects.create(name=org_name)
        Membership.objects.create(organization=org,user=user,role=Membership.Role.ADMIN)
    auth_login(request,user)
    return {'user_id':user.pk,'username':user.get_username(),'organization':{'id':org.pk,'name':org.name}}

@api.get('/orgs/{org_id}/parties')
def parties(request,org_id:int,kind:str|None=None):
    membership(request,org_id)
    qs=Party.objects.filter(organization_id=org_id)
    if kind:qs=qs.filter(kind=kind)
    return [{'id':p.pk,'kind':p.kind,'name':p.name,'phone':p.phone,'email':p.email} for p in qs.order_by('name')[:200]]

@api.post('/orgs/{org_id}/parties')
def create_party(request,org_id:int,payload:PartyIn):
    membership(request,org_id,('admin','accountant','operations'))
    if not payload.name.strip():raise HttpError(422,'Name is required')
    p=Party.objects.create(organization_id=org_id,**payload.dict())
    return {'id':p.pk,'kind':p.kind,'name':p.name}

@api.get('/orgs/{org_id}/vehicles')
def vehicles(request,org_id:int):
    membership(request,org_id)
    return [{'id':v.pk,'registration':v.registration,'model_name':v.model_name,'owner_id':v.owner_id,'owner':v.owner.name} for v in Vehicle.objects.filter(organization_id=org_id).select_related('owner').order_by('registration')[:200]]

@api.post('/orgs/{org_id}/vehicles')
def create_vehicle(request,org_id:int,payload:VehicleIn):
    membership(request,org_id,('admin','operations'))
    owner=Party.objects.filter(pk=payload.owner_id,organization_id=org_id,kind=Party.Kind.OWNER).first()
    if not owner:raise HttpError(422,'Owner is invalid')
    try:v=Vehicle.objects.create(organization_id=org_id,registration=payload.registration.strip(),model_name=payload.model_name.strip(),owner=owner)
    except IntegrityError:raise HttpError(409,'Registration already exists')
    return {'id':v.pk,'registration':v.registration,'model_name':v.model_name}

@api.get('/orgs/{org_id}/agreements')
def agreements(request,org_id:int,limit:int=100,offset:int=0):
    membership(request,org_id)
    qs=Agreement.objects.filter(organization_id=org_id).select_related('vehicle__owner','customer','driver').order_by('-id')
    return {'count':qs.count(),'items':[agreement_dict(a) for a in qs[max(0,offset):max(0,offset)+min(100,max(1,limit))]]}

@api.post('/orgs/{org_id}/agreements')
def create_agreement(request,org_id:int,payload:AgreementIn):
    membership(request,org_id,('admin','accountant','operations'))
    vehicle=Vehicle.objects.filter(pk=payload.vehicle_id,organization_id=org_id).select_related('owner').first()
    customer=Party.objects.filter(pk=payload.customer_id,organization_id=org_id,kind=Party.Kind.CUSTOMER).first()
    driver=Party.objects.filter(pk=payload.driver_id,organization_id=org_id,kind=Party.Kind.DRIVER).first()
    if not all([vehicle,customer,driver]):raise HttpError(422,'Vehicle, customer or driver is invalid')
    with transaction.atomic():
        if Agreement.objects.select_for_update().filter(vehicle=vehicle,active=True).filter(Q(end_date__isnull=True)|Q(end_date__gte=payload.start_date)).exists():raise HttpError(409,'Vehicle has an overlapping active agreement')
        a=Agreement(organization_id=org_id,**payload.dict())
        result(services.validate_agreement,a)
        a.save()
    return agreement_dict(Agreement.objects.select_related('vehicle__owner','customer','driver').get(pk=a.pk))

@api.post('/orgs/{org_id}/agreements/quick')
def quick_agreement(request,org_id:int,payload:QuickAgreementIn):
    membership(request,org_id,('admin','accountant','operations'))
    if not all(x.strip() for x in [payload.customer,payload.owner,payload.driver,payload.vehicle,payload.registration]):raise HttpError(422,'All parties and vehicle details are required')
    with transaction.atomic():
        if Vehicle.objects.filter(organization_id=org_id,registration__iexact=payload.registration.strip()).exists():raise HttpError(409,'Registration already exists; use the existing vehicle in the full agreement API')
        owner=Party.objects.create(organization_id=org_id,kind='owner',name=payload.owner.strip())
        customer=Party.objects.create(organization_id=org_id,kind='customer',name=payload.customer.strip())
        driver=Party.objects.create(organization_id=org_id,kind='driver',name=payload.driver.strip())
        vehicle=Vehicle.objects.create(organization_id=org_id,registration=payload.registration.strip(),model_name=payload.vehicle.strip(),owner=owner)
        a=Agreement(organization_id=org_id,vehicle=vehicle,customer=customer,driver=driver,start_date=payload.start,customer_rent=result(services.bd,payload.rent),owner_rent=result(services.bd,payload.owner_rent),driver_salary=result(services.bd,payload.salary),fuel_responsible=payload.fuel.lower(),repair_responsible=payload.repair.lower(),owner_offset_allowed=payload.offset)
        result(services.validate_agreement,a)
        a.save()
    return agreement_dict(Agreement.objects.select_related('vehicle__owner','customer','driver').get(pk=a.pk))

@api.post('/orgs/{org_id}/agreements/{agreement_id}/periods')
def issue_period(request,org_id:int,agreement_id:int,month:date):
    membership(request,org_id,('admin','accountant'))
    p=result(services.issue_period,membership(request,org_id).organization,agreement_id,month)
    return {'period_id':p.pk,'month':p.month.isoformat(),'obligations':[{'id':o.pk,'kind':o.kind,'party_id':o.party_id,'amount':str(o.amount)} for o in p.obligations.all()]}

@api.get('/orgs/{org_id}/periods')
def periods(request,org_id:int,month:date|None=None):
    membership(request,org_id)
    qs=Period.objects.filter(organization_id=org_id)
    if month:qs=qs.filter(month=month)
    return [{'id':p.pk,'agreement_id':p.agreement_id,'month':p.month.isoformat(),'closed':bool(p.closed_at)} for p in qs.order_by('-month','agreement_id')[:200]]

@api.get('/orgs/{org_id}/periods/{period_id}/statement')
def statement(request,org_id:int,period_id:int):
    membership(request,org_id)
    p=Period.objects.filter(pk=period_id,organization_id=org_id).first()
    if not p:raise HttpError(404,'Period not found')
    return money_dict(result(services.statement,p))

@api.post('/orgs/{org_id}/expenses')
def record_expense(request,org_id:int,payload:ExpenseIn):
    m=membership(request,org_id,('admin','accountant','operations'))
    e=result(services.record_expense,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''))
    return expense_dict(e)

@api.get('/orgs/{org_id}/expenses')
def expenses(request,org_id:int,period_id:int|None=None):
    membership(request,org_id)
    qs=Expense.objects.filter(period__organization_id=org_id)
    if period_id:qs=qs.filter(period_id=period_id)
    return [expense_dict(e) for e in qs.order_by('-id')[:200]]

@api.post('/orgs/{org_id}/expenses/{expense_id}/approve')
def approve_expense(request,org_id:int,expense_id:int):
    membership(request,org_id,('admin','accountant'))
    return expense_dict(result(services.approve_expense,membership(request,org_id).organization,request.user,expense_id))

@api.post('/orgs/{org_id}/expenses/{expense_id}/reverse')
def reverse_expense(request,org_id:int,expense_id:int,payload:ReasonIn):
    membership(request,org_id,('admin','accountant'))
    return expense_dict(result(services.reverse_expense,membership(request,org_id).organization,request.user,expense_id,payload.reason))

@api.post('/orgs/{org_id}/payments')
def record_payment(request,org_id:int,payload:PaymentIn):
    m=membership(request,org_id,('admin','accountant'))
    p=result(services.record_payment,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''))
    return payment_dict(Payment.objects.prefetch_related('allocations').get(pk=p.pk))

@api.get('/orgs/{org_id}/payments')
def payments(request,org_id:int):
    membership(request,org_id)
    return [payment_dict(p) for p in Payment.objects.filter(organization_id=org_id).prefetch_related('allocations').order_by('-id')[:200]]

@api.post('/orgs/{org_id}/periods/{period_id}/close')
def close_period(request,org_id:int,period_id:int):
    m=membership(request,org_id,('admin','accountant'))
    p=result(services.close_period,m.organization,request.user,period_id)
    return {'period_id':p.pk,'closed':True,'closed_at':p.closed_at.isoformat()}

@api.get('/orgs/{org_id}/notifications')
def notifications(request,org_id:int):
    membership(request,org_id)
    return [{'id':n.pk,'event_type':n.event_type,'recipient':n.recipient.name,'state':n.state,'attempts':n.attempts,'created_at':n.created_at.isoformat()} for n in NotificationOutbox.objects.filter(organization_id=org_id).select_related('recipient').order_by('-id')[:200]]

@api.post('/orgs/{org_id}/notifications/{notice_id}/retry')
def retry_notice(request,org_id:int,notice_id:int):
    membership(request,org_id,('admin','accountant'))
    notice=NotificationOutbox.objects.filter(pk=notice_id,organization_id=org_id).first()
    if not notice:raise HttpError(404,'Notice not found')
    if notice.state=='delivered':raise HttpError(422,'Delivered messages do not need retry')
    notice.state='queued';notice.last_error='';notice.save(update_fields=['state','last_error'])
    return {'id':notice.pk,'state':notice.state}

@api.get('/orgs/{org_id}/snapshot')
def snapshot(request,org_id:int,month:date):
    membership(request,org_id,('admin','accountant'))
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
            entries.append({'id':public_id,'agreementId':agreement_id,'type':'Expense','amount':float(e.amount),'date':e.event_date.isoformat(),'category':e.category.title(),'paidBy':e.paid_by.title(),'responsible':e.responsible.title(),'reference':e.reference,'description':e.description or e.category.title(),'status':{'pending':'Pending approval','posted':'Posted','reversed':'Reversed'}[e.state],'offset':e.offset_owner})
        for alloc in p.obligations.all():
            for row in alloc.allocations.select_related('payment').all():
                pay=row.payment
                entries.append({'id':f'PAY-{pay.pk}-{row.pk}','agreementId':agreement_id,'type':{'customer_payment':'Customer payment','owner_payout':'Owner payout','driver_payout':'Driver payout','salary_advance':'Salary advance'}[pay.kind],'amount':float(row.amount),'date':pay.event_date.isoformat(),'category':'Rent' if pay.kind=='customer_payment' else 'Payout','paidBy':pay.party.name if pay.kind=='customer_payment' else 'Operator','responsible':'Operator','reference':pay.reference,'description':pay.get_kind_display(),'status':'Posted'})
    notices=[{'id':f'NT-{n.pk}','title':n.event_type.replace('_',' ').title(),'detail':str(n.payload),'date':n.created_at.date().isoformat(),'status':n.state.title(),'read':False,'recipient':n.recipient.name} for n in NotificationOutbox.objects.filter(organization_id=org_id).select_related('recipient').order_by('-id')[:200]]
    return {'agreements':agreements,'entries':entries,'notices':notices,'closed':closed,'backend':{'orgId':org_id,'periodIds':period_ids,'obligationIds':obligation_ids,'expenseIds':expense_ids}}
