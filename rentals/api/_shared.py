"""Schemas and helpers shared by every router: membership scoping, error mapping, dict serializers."""
from datetime import date
from decimal import Decimal
from typing import Literal
from django.db import IntegrityError
from django.http import HttpRequest
from ninja import Schema
from ninja.errors import HttpError
from ..models import Membership, Party, Vehicle, Agreement, Period, Obligation, Expense
from .. import services

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
