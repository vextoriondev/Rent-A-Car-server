"""One role -> action table plus object scoping. Every endpoint asks `can`/`require`; nothing else decides access.

Staff roles act on the whole organization. Actor roles (owner/driver/customer) only ever see their own records,
and `redact_agreement` strips the terms that belong to the other parties."""
from django.db.models import Q

STAFF_ROLES={'admin','accountant','operations','viewer','mechanic'}
ORG_WIDE_ROLES={'admin','accountant','operations','viewer'}
ACTOR_ROLES={'owner','driver','customer'}
FINANCE={'admin','accountant'}

ROLE_ACTIONS: dict[str,set[str]] = {
    # generic spec verbs
    'read':STAFF_ROLES|ACTOR_ROLES,                 # always passed through scope_queryset
    'read_financial':ORG_WIDE_ROLES,
    'draft':{'admin','accountant','operations'},
    'approve':FINANCE,'post':FINANCE,'reverse':FINANCE,'settle':FINANCE,'reopen':{'admin'},
    'export':{'admin','accountant','viewer'},
    'sensitive':FINANCE,                            # consent, contacts, logins, other parties' terms
    # concrete endpoint actions (same roles the pre-M1 membership() tuples allowed)
    'party.write':{'admin','accountant','operations'},
    'vehicle.write':{'admin','operations'},
    'agreement.write':{'admin','accountant','operations'},
    'expense.record':{'admin','accountant','operations'},
    'period.issue':FINANCE,'expense.approve':FINANCE,'expense.reverse':FINANCE,'payment.post':FINANCE,
    'period.close':FINANCE,'notice.retry':FINANCE,'snapshot.read':FINANCE,
    'agreement.revise':FINANCE,'ownership.transfer':FINANCE,'driver.assign':{'admin','accountant','operations'},
    'expense.override':FINANCE,'rules.write':FINANCE,'category.write':{'admin'},
    'charge.post':FINANCE,'invoice.issue':FINANCE,'ledger.read':{'admin','accountant','viewer'},
    'settlement.prepare':FINANCE,'settings.write':{'admin'},
    'maintenance.request':{'admin','accountant','operations','mechanic'},'maintenance.manage':{'admin','accountant','operations'},'maintenance.approve':FINANCE,'maintenance.read':STAFF_ROLES,
    'audit.read':{'admin','accountant'},'document.upload':{'admin','accountant','operations','mechanic'},'document.read':STAFF_ROLES,'document.delete':{'admin','accountant','operations'},
    'branch.write':{'admin'},'access.manage':{'admin'},
}

def can(role:str,action:str) -> bool:
    return role in ROLE_ACTIONS.get(action,set())

def is_actor(role:str) -> bool: return role in ACTOR_ROLES

def actor_party_ids(user,org_id) -> set[int]:
    from .models import PartyUser
    return set(PartyUser.objects.filter(organization_id=org_id,user=user,active=True).values_list('party_id',flat=True))

def limited_branch(member):
    """The branch a member is confined to, or None. Admins and members without a branch see every branch."""
    return member.branch_id if member.branch_id and member.role!='admin' else None

def effective_branch(member,requested=None):
    """A branch-limited member is forced to their own branch whatever they ask for; everyone else may optionally look at one branch."""
    return limited_branch(member) or requested

def _branch_paths():
    from .models import Vehicle, Agreement, AgreementVersion, Period, Obligation, Expense, Charge, Invoice, Settlement, MaintenanceCase
    return {Vehicle:'branch',Agreement:'branch',AgreementVersion:'agreement__branch',Period:'agreement__branch',Obligation:'period__agreement__branch',Expense:'period__agreement__branch',
        Charge:'agreement__branch',Invoice:'period__agreement__branch',Settlement:'agreement__branch',MaintenanceCase:'vehicle__branch'}

def branch_parties(user_id,branch_id):
    """Parties a branch may see: those on its agreements or vehicles, plus unlinked ones the user created (so a new party can be picked before its first agreement)."""
    from .models import Party, Vehicle, Agreement
    linked=Q(pk__in=Agreement.objects.filter(branch_id=branch_id).values('customer_id'))|Q(pk__in=Agreement.objects.filter(branch_id=branch_id).values('driver_id'))|Q(pk__in=Vehicle.objects.filter(branch_id=branch_id).values('owner_id'))
    anywhere=Q(pk__in=Agreement.objects.values('customer_id'))|Q(pk__in=Agreement.objects.values('driver_id'))|Q(pk__in=Vehicle.objects.values('owner_id'))
    return Party.objects.filter(linked|(Q(created_by_id=user_id)&~anywhere)).values('pk')

def branch_scope(member,qs,branch_id=None):
    """Narrow qs to one branch: always for a branch-limited member, on request for an admin. Models without a branch are returned unchanged."""
    from .models import Party, Payment, Allocation, NotificationOutbox
    branch=effective_branch(member,branch_id)
    if not branch:return qs
    model=qs.model
    if model is Party:return qs.filter(pk__in=branch_parties(member.user_id,branch))
    if model is Payment:return qs.filter(pk__in=Allocation.objects.filter(obligation__period__agreement__branch_id=branch).values('payment_id'))
    if model is NotificationOutbox:return qs.filter(recipient_id__in=branch_parties(member.user_id,branch))
    path=_branch_paths().get(model)
    return qs.filter(**{path:branch}) if path else qs

def scope_queryset(user,member,qs,branch_id=None):
    """Restrict qs to what `member` may see. Org-wide staff: the organization, narrowed to their branch when they have one (`branch_id` lets an admin look at one branch). Actors: only their own parties,
    vehicles, agreements, periods and notices. Mechanics see vehicles only."""
    from .models import Party, Vehicle, Agreement, Period, NotificationOutbox
    model=qs.model;org=member.organization_id;role=member.role
    if role in ORG_WIDE_ROLES:return branch_scope(member,qs.filter(organization_id=org),branch_id)
    if role=='mechanic':return branch_scope(member,qs.filter(organization_id=org)) if model is Vehicle else qs.none()
    if role not in ACTOR_ROLES:return qs.none()
    parties=actor_party_ids(user,org)
    link={'owner':Q(vehicle__owner_id__in=parties),'driver':Q(driver_id__in=parties),'customer':Q(customer_id__in=parties)}[role]
    agreements=Agreement.objects.filter(link,organization_id=org)
    if model is Party:return qs.filter(organization_id=org,pk__in=parties)
    if model is Vehicle:return qs.filter(organization_id=org).filter(Q(owner_id__in=parties) if role=='owner' else Q(pk__in=agreements.values('vehicle_id')))
    if model is Agreement:return qs.filter(pk__in=agreements.values('pk'))
    if model is Period:return qs.filter(organization_id=org,agreement__in=agreements)
    if model is NotificationOutbox:return qs.filter(organization_id=org,recipient_id__in=parties)
    return qs.none()

def redact_agreement(data:dict,role:str) -> dict:
    """Actors see only their own side of the terms: owner -> owner rent; driver -> salary; customer -> rent."""
    if role not in ACTOR_ROLES:return data
    hidden={'customer_rent','owner_rent','driver_salary','owner_offset_allowed','fuel_responsible','repair_responsible'}
    own={'owner':'owner_rent','driver':'driver_salary','customer':'customer_rent'}[role]
    out={k:v for k,v in data.items() if k not in hidden}
    out[own]=data[own]
    for other in {'owner','customer','driver'}-{role}:out.pop(other,None)
    return out
