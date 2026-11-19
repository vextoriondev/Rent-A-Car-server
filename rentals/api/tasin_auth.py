"""Authentication + authorization for the Tasin routers.

One place decides, for every request under `/orgs/{org_id}/...`:
  1. the caller has a valid session (CSRF is enforced by SessionAuth for unsafe methods)  -> else 401
  2. the caller is a member of that organization (or is a Super Admin)                    -> else 403
  3. the membership is not an external actor role (vehicle owner / driver / customer portal logins) -> else 403
  4. the caller holds the RBAC module action the route needs (view/create/edit/delete/export) -> else 403
Routes that match no rule are denied (fail closed) unless the caller is a Super Admin.
The identity is always `request.user`; headers, query strings and payload fields never choose who the caller is.
"""
import re
from ninja.security import SessionAuth
from ..models import Organization, Membership, Party
from ..policies import is_actor
from ..rbac import has_module_action, is_super_admin
from ..services.finance import FinanceError

ORG_PATH = re.compile(r'/orgs/(\d+)/(.*?)/?$')
ANY_MEMBER = '*'
PARTY = '<party>'   # module depends on the party's kind: owner -> owners, driver -> drivers, customer -> clients
PARTY_MODULE = {'owner': 'owners', 'driver': 'drivers', 'customer': 'clients'}

# (regex on the part after /orgs/{id}/, module). First match wins.
RULES = [
    (r'^parties/\d+/', PARTY),
    (r'^(drivers/\d+/(finance|advances|fuel-gas|salary-payments)|salary-payments/\d+/reverse|replacements)(/|$)', 'drivers'),
    (r'^(owners/\d+/(finance|ledger-payments)|rent-accruals)(/|$)', 'owners'),
    (r'^clients/\d+/(finance|ledger-payments)$', 'clients'),
    (r'^vehicles/\d+/documents$', 'agreements'),
    (r'^vehicles/\d+/(payments|billing)$', 'accounts'),
    (r'^transactions(/|$)', 'accounts'),
    (r'^daily-report$', 'expenses'),
    (r'^(tasin-agreements|documents-all)(/|$)', 'agreements'),
    (r'^(garages/\d+/(statement|payments)|garage-payments/\d+/reverse)$', 'maintenance'),
    (r'^finance-settings$', 'settings'),
    (r'^owners/\d+/charges$', 'owners'),
    (r'^ledger-entries/\d+/reverse$', 'drivers'),
    (r'^expense-summary$', 'expenses'),
    (r'^dashboard(/|$)', 'dashboard'),
    (r'^tasin-owners(/|$)', 'owners'),
    (r'^tasin-vehicles(/|$)', 'vehicles'),
    (r'^(tasin-drivers|driver-attendance)(/|$)', 'drivers'),
    (r'^(tasin-clients|tasin-invoices)(/|$)', 'clients'),
    (r'^(tasin-duties|duties)(/|$)', 'duties'),
    (r'^tasin-expenses(/|$)', 'expenses'),
    (r'^(tasin-garages|tasin-maintenance|spare-parts)(/|$)', 'maintenance'),
    (r'^(tasin-accounts|tasin-transactions|bank-reconciliation)(/|$)', 'accounts'),
    (r'^(tasin-due-collection|due-followups)(/|$)', 'due_collection'),
    (r'^tasin-vehicle-search(/|$)', 'vehicle_search'),
    (r'^(tasin-alerts|custom-reminders)(/|$)', 'notifications'),
    (r'^(tasin-reports|reports|custom-report-builder)(/|$)', 'reports'),
    (r'^tasin-settings/company$', ANY_MEMBER),          # company name/logo is needed on every screen
    (r'^tasin-settings(/|$)', 'settings'),
    (r'^backup(/|$)', 'settings'),
    (r'^files-usage$', 'settings'),
    (r'^files(-limits)?(/|$)', ANY_MEMBER),            # the handler checks the module of the file's own record (owners, drivers, vehicles, ...)
    (r'^(documents|private-documents|agreements)(/|$)', 'agreements'),
    (r'^(access-requests|rbac)(/|$)', ANY_MEMBER),      # a user without a module asks for it here; handlers check admin rights themselves
]
RULES = [(re.compile(p), m) for p, m in RULES]
ACTION_BY_METHOD = {'GET': 'view', 'HEAD': 'view', 'OPTIONS': 'view', 'POST': 'create', 'PUT': 'edit', 'PATCH': 'edit', 'DELETE': 'delete'}


def required_permission(method: str, rest: str):
    """(module, action) a request needs, or None when no rule covers the route."""
    module = next((m for rx, m in RULES if rx.search(rest)), None)
    if module is None:
        return None
    action = ACTION_BY_METHOD.get(method.upper(), 'edit')
    if rest.endswith('/read') or rest.startswith('custom-report-builder'):
        action = 'view'                                   # read-only POSTs
    elif rest.endswith('/status'):
        action = 'edit'
    elif rest.startswith('backup'):
        action = 'export'
    return module, action


def authorize_org(user, org, module: str, action: str = 'view'):
    """Raise FinanceError(403) unless `user` may perform `action` on `module` in `org`."""
    sa = is_super_admin(user, org)
    membership = Membership.objects.filter(user=user, organization=org).first()
    if not sa:
        if not membership:
            raise FinanceError('forbidden', 'You do not have access to this organization', status=403)
        if is_actor(membership.role):
            raise FinanceError('forbidden', 'Portal logins cannot use the management workspace', status=403)
    if module == ANY_MEMBER or sa:
        return
    if not has_module_action(user, org, module, action):
        raise FinanceError('forbidden', f'You do not have permission to {action} {module.replace("_", " ")}', status=403)


class TasinAuth(SessionAuth):
    """Session login (+CSRF) and per-organization RBAC, evaluated before the endpoint runs."""

    def authenticate(self, request, key):
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return None
        match = ORG_PATH.search(request.path)
        if match:
            org = Organization.objects.filter(pk=int(match.group(1))).first()
            if not org:
                raise FinanceError('not_found', 'Organization not found', status=404)
            needed = required_permission(request.method, match.group(2))
            if needed is None:
                if not is_super_admin(user, org):
                    raise FinanceError('forbidden', 'This route is not available', status=403)
                return user
            module, action = needed
            if module == PARTY:
                authorize_org(user, org, ANY_MEMBER)  # membership first, so non-members cannot probe which ids exist
                party = Party.objects.filter(pk=int(re.search(r'parties/(\d+)', match.group(2)).group(1)), organization=org).first()
                if not party:
                    raise FinanceError('not_found', 'Record not found', status=404)
                module = PARTY_MODULE[party.kind]
            authorize_org(user, org, module, action)
        return user


tasin_auth = TasinAuth()
