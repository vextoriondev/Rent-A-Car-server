"""Schemas and helpers shared by every router: membership scoping, error mapping, dict serializers."""
from datetime import date
from decimal import Decimal
from typing import Literal
from django.db import IntegrityError
from django.http import HttpRequest
from ninja import Schema
from ninja.errors import HttpError
from ..models import Membership, Party, Vehicle, Agreement, Period, Obligation, Expense, Contact, AgreementVersion, Invoice, Settlement, MaintenanceCase, Document
from .. import services, policies

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
    draft:bool=False; receipt_reference:str=''; override_reason:str=''; allow_duplicate_reason:str=''; late_entry:bool=False; maintenance_case_id:int|None=None
class ExpensePatch(Schema):
    category:str|None=None; amount:Decimal|None=None; event_date:date|None=None; paid_by:Literal['operator','owner']|None=None; responsible:Literal['owner','operator','customer']|None=None; offset_owner:bool|None=None; reference:str|None=None; description:str|None=None
    receipt_reference:str|None=None; override_reason:str|None=None; allow_duplicate_reason:str|None=None
class CategoryIn(Schema): code:str; name:str
class RuleIn(Schema):
    responsible:Literal['owner','operator','customer']; first_payer:Literal['operator','owner']='operator'; reimbursable:bool=True; deductible_from:Literal['owner_payable','none']='none'
    approval_threshold:Decimal|None=None; proof_required:bool=False; notify_parties:bool=True
class AllocationIn(Schema): obligation_id:int; amount:Decimal
class PaymentIn(Schema):
    kind:Literal['customer_payment','owner_payout','driver_payout','salary_advance']
    amount:Decimal; reference:str; method:str='bank'; event_date:date; allocations:list[AllocationIn]
class ReasonIn(Schema): reason:str
class CaseIn(Schema): vehicle_id:int; issue:str; vendor:str=''; quote:Decimal|None=None; downtime_from:date|None=None; downtime_to:date|None=None
class CasePatch(Schema): issue:str|None=None; vendor:str|None=None; quote:Decimal|None=None; downtime_from:date|None=None; downtime_to:date|None=None; status:Literal['in_progress','done','cancelled']|None=None
class SettingsIn(Schema): cutoff_day:int|None=None; approval_role:Literal['admin','accountant']|None=None; negative_owner_blocks_close:bool|None=None
class ChargeIn(Schema):
    period_id:int; side:Literal['customer','owner','driver']; kind:Literal['charge','credit','adjustment','reimbursement','opening_balance']
    amount:Decimal; direction:Literal['increase','decrease']|None=None; description:str=''; reference:str=''
class InvoiceIn(Schema): period_id:int; obligation_ids:list[int]|None=None; issue_date:date|None=None; due_date:date|None=None
class TermsIn(Schema):
    customer_rent:Decimal|None=None; owner_rent:Decimal|None=None; driver_salary:Decimal|None=None
    fuel_responsible:Literal['owner','operator','customer']|None=None; repair_responsible:Literal['owner','operator','customer']|None=None
    owner_offset_allowed:bool|None=None; approval_threshold:Decimal|None=None
class ReviseIn(Schema): effective_from:date; reason:str; changes:TermsIn
class AssignDriverIn(Schema): driver_id:int; effective_from:date; salary_override:Decimal|None=None
class TransferOwnerIn(Schema): owner_id:int; effective_from:date
class DraftIn(Schema): draft:bool=False
class PartyPatch(Schema): name:str|None=None; phone:str|None=None; email:str|None=None
class ConsentIn(Schema): sms:bool|None=None; whatsapp:bool|None=None; email:bool|None=None; preferred_channel:str|None=None; source:str=''
class ContactIn(Schema): name:str; phone:str=''; email:str=''; role:Literal['billing','operations','primary','other']='other'
class AccessIn(Schema): username:str; branch_id:int|None=None
class BranchIn(Schema): name:str
class LoginIn(Schema): username:str; password:str
class RegisterIn(Schema): organization_name:str; username:str; email:str=''; password:str
class QuickAgreementIn(Schema):
    customer:str; owner:str; driver:str; vehicle:str; registration:str; start:date
    rent:Decimal; owner_rent:Decimal; salary:Decimal
    fuel:Literal['Owner','Operator','Customer']='Owner'
    repair:Literal['Owner','Operator','Customer']='Owner'
    offset:bool=False

def membership(request:HttpRequest,org_id:int,action:str|None=None):
    """The caller's membership in org_id (404 if none, so other organizations stay invisible); `action` is checked against policies."""
    member=Membership.objects.filter(organization_id=org_id,user=request.user).select_related('organization','branch').first()
    if not member:raise HttpError(404,'Organization not found')
    if action and not policies.can(member.role,action):raise HttpError(403,'Your role cannot perform this action')
    return member

def result(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except services.DomainError as exc:raise HttpError(422,str(exc)) from exc
    except (Period.DoesNotExist,Agreement.DoesNotExist,Expense.DoesNotExist,Party.DoesNotExist,Vehicle.DoesNotExist,Obligation.DoesNotExist,Contact.DoesNotExist,AgreementVersion.DoesNotExist,Invoice.DoesNotExist,Settlement.DoesNotExist,MaintenanceCase.DoesNotExist,Document.DoesNotExist):raise HttpError(404,'Record not found')
    except IntegrityError as exc:raise HttpError(409,'Duplicate or conflicting record') from exc

def agreement_dict(a):
    return {'id':a.pk,'vehicle':{'id':a.vehicle_id,'model_name':a.vehicle.model_name,'registration':a.vehicle.registration},'owner':{'id':a.vehicle.owner_id,'name':a.vehicle.owner.name},'customer':{'id':a.customer_id,'name':a.customer.name},'driver':{'id':a.driver_id,'name':a.driver.name},'start_date':a.start_date.isoformat(),'end_date':a.end_date.isoformat() if a.end_date else None,'customer_rent':str(a.customer_rent),'owner_rent':str(a.owner_rent),'driver_salary':str(a.driver_salary),'fuel_responsible':a.fuel_responsible,'repair_responsible':a.repair_responsible,'owner_offset_allowed':a.owner_offset_allowed}

def party_dict(p,sensitive=False):
    data={'id':p.pk,'kind':p.kind,'name':p.name,'phone':p.phone,'email':p.email}
    if sensitive:data.update({'preferred_channel':p.preferred_channel,'consent':{'sms':p.consent_sms,'whatsapp':p.consent_whatsapp,'email':p.consent_email,'updated_at':p.consent_updated_at.isoformat() if p.consent_updated_at else None,'source':p.consent_source},'contacts':[{'id':c.pk,'name':c.name,'phone':c.phone,'email':c.email,'role':c.role} for c in p.contacts.all()]})
    return data

def version_dict(v):
    return {'id':v.pk,'number':v.number,'effective_from':v.effective_from.isoformat(),'status':v.status,'reason':v.reason,'customer_rent':str(v.customer_rent),'owner_rent':str(v.owner_rent),'driver_salary':str(v.driver_salary),'fuel_responsible':v.fuel_responsible,'repair_responsible':v.repair_responsible,'owner_offset_allowed':v.owner_offset_allowed,'approval_threshold':str(v.approval_threshold),'billing_cadence':v.billing_cadence,'payout_cadence':v.payout_cadence}

def rule_dict(r):
    return {'id':r.pk,'category':r.category,'responsible':r.responsible,'first_payer':r.first_payer,'reimbursable':r.reimbursable,'deductible_from':r.deductible_from,'approval_threshold':str(r.approval_threshold) if r.approval_threshold is not None else None,'proof_required':r.proof_required,'notify_parties':r.notify_parties}

def agreement_detail(a):
    d=agreement_dict(a);d['status']=a.status
    d['versions']=[{**version_dict(v),'rules':[rule_dict(r) for r in v.expense_rules.order_by('category')]} for v in a.versions.order_by('number')]
    d['driver_assignments']=[{'driver_id':x.driver_id,'driver':x.driver.name,'effective_from':x.effective_from.isoformat(),'effective_to':x.effective_to.isoformat() if x.effective_to else None,'salary_override':str(x.salary_override) if x.salary_override is not None else None} for x in a.driver_assignments.select_related('driver').order_by('effective_from')]
    d['ownerships']=[{'owner_id':x.owner_id,'owner':x.owner.name,'effective_from':x.effective_from.isoformat(),'effective_to':x.effective_to.isoformat() if x.effective_to else None} for x in a.vehicle.ownerships.select_related('owner').order_by('effective_from')]
    return d

def charge_dict(c):
    return {'id':c.pk,'kind':c.kind,'direction':c.direction,'side':c.side,'party_id':c.party_id,'period_id':c.period_id,'amount':str(c.amount),'description':c.description,'reference':c.reference,'obligation_id':c.obligation_id}

def invoice_dict(i,summary):
    return {'id':i.pk,'number':i.number,'party_id':i.party_id,'period_id':i.period_id,'issue_date':i.issue_date.isoformat(),'due_date':i.due_date.isoformat(),'state':i.state,'status':summary['status'],'amount':str(summary['amount']),'paid':str(summary['paid']),'outstanding':str(summary['outstanding']),'lines':[{'obligation_id':l.obligation_id,'description':l.description,'amount':str(l.amount)} for l in i.lines.all()]}

def settlement_dict(s,lines=False):
    d={'id':s.pk,'period_id':s.period_id,'agreement_id':s.agreement_id,'month':s.month.isoformat(),'state':s.state,'version':s.version,'prepared_at':s.prepared_at.isoformat() if s.prepared_at else None,'approved_at':s.approved_at.isoformat() if s.approved_at else None,'closed_at':s.closed_at.isoformat() if s.closed_at else None,'reopen_reason':s.reopen_reason}
    if lines:d['lines']=[{'section':l.section,'label':l.label,'amount':str(l.amount),'source_type':l.source_type,'source_id':l.source_id} for l in s.lines.all()]
    return d

def case_dict(c):
    return {'id':c.pk,'vehicle_id':c.vehicle_id,'issue':c.issue,'vendor':c.vendor,'quote':str(c.quote) if c.quote is not None else None,'downtime_from':c.downtime_from.isoformat() if c.downtime_from else None,'downtime_to':c.downtime_to.isoformat() if c.downtime_to else None,'status':c.status,'reported_by':c.reported_by_id,'approved_at':c.approved_at.isoformat() if c.approved_at else None}

def document_dict(d):
    return {'id':d.pk,'target_type':d.target_type,'target_id':d.target_id,'name':d.original_name,'content_type':d.content_type,'size':d.size,'sha256':d.sha256,'created_at':d.created_at.isoformat()}

def audit_dict(a):
    return {'id':a.pk,'at':a.at.isoformat(),'actor_id':a.actor_id,'action':a.action,'target_type':a.target_type,'target_id':a.target_id,'before':a.before,'after':a.after,'reason':a.reason,'request_id':a.request_id,'ip':a.ip}

def paginate(qs,limit,offset,serialize):
    """Shared list envelope {count, items, next}; next is the offset of the following page or null. Limit is capped at 200."""
    limit=min(200,max(1,limit));offset=max(0,offset);count=qs.count()
    return {'count':count,'items':[serialize(x) for x in qs[offset:offset+limit]],'next':offset+limit if offset+limit<count else None}

def money_dict(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,dict):return {k:money_dict(v) for k,v in value.items()}
    return value

def expense_dict(e):
    return {'id':e.pk,'public_id':e.public_id,'period_id':e.period_id,'amount':str(e.amount),'category':e.category,'paid_by':e.paid_by,'responsible':e.responsible,'offset_owner':e.offset_owner,'reference':e.reference,'state':e.state,'rule_snapshot':e.rule_snapshot,'reversal_reason':e.reversal_reason,'receipt_reference':e.receipt_reference,'override_reason':e.override_reason,'duplicate_reason':e.duplicate_reason,'rule_id':e.rule_id,'maintenance_case_id':e.maintenance_case_id}

def payment_dict(p):
    return {'id':p.pk,'public_id':p.public_id,'kind':p.kind,'party_id':p.party_id,'amount':str(p.amount),'reference':p.reference,'allocations':[{'obligation_id':x.obligation_id,'amount':str(x.amount)} for x in p.allocations.all()]}
