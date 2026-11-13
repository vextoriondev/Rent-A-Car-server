"""
TASIN ENTERPRISE — Granular Dynamic Role-Based Access Control (RBAC) & Permission Engine
Supports:
  1. Super Admin (Highest authority, unlimited access, manages all users and approvals).
  2. Main Admin (Delegated administrator, restricted within granted authority).
  3. Dynamic Module & Action Permissions stored in DB per user across all 15 modules.
  4. Employee-initiated workspace/module access requests with Admin approval workflow.
"""

from functools import wraps
from typing import List, Optional, Dict, Any
from django.http import HttpRequest
from django.contrib.auth import get_user_model
from ninja.errors import HttpError
from .models import Membership, Organization

# ─── 15 OFFICIAL APPLICATION MODULES ──────────────────────────────────────────

MODULE_REGISTRY = [
    {'key': 'dashboard', 'name': 'Dashboard', 'description': 'Executive KPIs, fleet metrics, and monthly charts'},
    {'key': 'owners', 'name': 'Owners Management', 'description': 'Vehicle owners, contracts, and monthly payables'},
    {'key': 'vehicles', 'name': 'Vehicles Management', 'description': 'Fleet directory, specs, mobil counter, and papers'},
    {'key': 'drivers', 'name': 'Drivers & Attendance', 'description': 'Driver roster, daily attendance register, licenses'},
    {'key': 'clients', 'name': 'Clients & Companies', 'description': 'Corporate clients, profiles, and trade licenses'},
    {'key': 'duties', 'name': 'Duties & Trips', 'description': 'Daily trip assignments, slips, and dispatch sheets'},
    {'key': 'expenses', 'name': 'Daily Expenses', 'description': 'Fuel, mobil, toll, maintenance expense vouchers'},
    {'key': 'maintenance', 'name': 'Maintenance & Garage', 'description': 'Workshop partners, repair jobs, and spare parts'},
    {'key': 'agreements', 'name': 'Agreements & Documents', 'description': 'Vehicle & client agreements, renewals, private files'},
    {'key': 'accounts', 'name': 'Accounts & Transactions', 'description': 'Cash & bank accounts, ledger transfers, reconciliation'},
    {'key': 'due_collection', 'name': 'Due & Collection', 'description': 'Receivables, aging, follow-up logs, and payouts'},
    {'key': 'vehicle_search', 'name': 'Vehicle Search & Statements', 'description': 'Global registration search, duty timeline, and logs'},
    {'key': 'reports', 'name': 'Reports & Analytics', 'description': 'Vehicle P&L, ledger statements, custom report builder'},
    {'key': 'notifications', 'name': 'Notifications & Reminders', 'description': 'Document expiry alerts and custom scheduled reminders'},
    {'key': 'settings', 'name': 'Settings & User Management', 'description': 'RBAC matrix, user accounts, and database backup'}
]

ALL_MODULE_KEYS = [m['key'] for m in MODULE_REGISTRY]

ROLES = {
    'SUPER_ADMIN': 'super_admin',
    'ADMIN': 'admin',
    'ACCOUNTANT': 'accountant',
    'OPERATIONS_MANAGER': 'operations_manager',
    'STAFF': 'staff',
}

# ─── DEFAULT BASELINE PERMISSION MAPS FOR NEW USERS ───────────────────────────

DEFAULT_ROLE_MODULE_ACTIONS: Dict[str, Dict[str, Dict[str, bool]]] = {
    'super_admin': {
        m: {'view': True, 'create': True, 'edit': True, 'delete': True, 'export': True, 'approve': True}
        for m in ALL_MODULE_KEYS
    },
    'admin': {
        'dashboard': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'owners': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'vehicles': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'drivers': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'clients': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'duties': {'view': True, 'create': True, 'edit': True, 'delete': True, 'export': True, 'approve': True},
        'expenses': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'maintenance': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'agreements': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'accounts': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'due_collection': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'vehicle_search': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'reports': {'view': True, 'create': True, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'notifications': {'view': True, 'create': True, 'edit': True, 'delete': True, 'export': False, 'approve': False},
        'settings': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': False},
    },
    'accountant': {
        'dashboard': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'owners': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'vehicles': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'drivers': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'clients': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': False},
        'duties': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'expenses': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': False},
        'maintenance': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'agreements': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'accounts': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'due_collection': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'vehicle_search': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'reports': {'view': True, 'create': True, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'notifications': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'settings': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
    },
    'operations_manager': {
        'dashboard': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'owners': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'vehicles': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'drivers': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'clients': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'duties': {'view': True, 'create': True, 'edit': True, 'delete': True, 'export': True, 'approve': True},
        'expenses': {'view': True, 'create': True, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'maintenance': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': True, 'approve': True},
        'agreements': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'accounts': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'due_collection': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'vehicle_search': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': True, 'approve': False},
        'reports': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'notifications': {'view': True, 'create': True, 'edit': True, 'delete': False, 'export': False, 'approve': False},
        'settings': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
    },
    'staff': {
        'dashboard': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'owners': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'vehicles': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'drivers': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'clients': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'duties': {'view': True, 'create': True, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'expenses': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'maintenance': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'agreements': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'accounts': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'due_collection': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'vehicle_search': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'reports': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'notifications': {'view': True, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
        'settings': {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False},
    }
}

# Backward compatible ROLE_PERMISSIONS string array
ROLE_PERMISSIONS: Dict[str, List[str]] = {
    role: [
        f"{mod}:{act}"
        for mod, acts in mods.items()
        for act, allowed in acts.items()
        if allowed
    ]
    for role, mods in DEFAULT_ROLE_MODULE_ACTIONS.items()
}

# ─── CORE USER ROLES & PERMISSIONS RESOLUTION ────────────────────────────────

def is_super_admin(user, org: Optional[Organization] = None) -> bool:
    """Super Admin is the highest authority and is never restricted."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if org:
        m = Membership.objects.filter(user=user, organization=org).first()
        if m and m.role.lower() in ('super_admin', 'superadmin', 'owner'):
            return True
    return False

def is_main_admin(user, org: Organization) -> bool:
    """Main Admin account with delegated administration powers."""
    if is_super_admin(user, org):
        return True
    if not user or not user.is_authenticated:
        return False
    m = Membership.objects.filter(user=user, organization=org).first()
    if m and m.role.lower() in ('admin', 'administrator'):
        return True
    if hasattr(user, 'tasin_profile') and user.tasin_profile.is_main_admin:
        return True
    return False

def get_user_role(user, org: Organization) -> str:
    """Returns normalized role string for user in org."""
    if not user or not user.is_authenticated:
        return 'staff'
    if is_super_admin(user, org):
        return 'super_admin'
    
    m = Membership.objects.filter(user=user, organization=org).first()
    if m:
        r = (m.role or 'staff').lower().replace('-', '_').replace(' ', '_')
        if r in ROLES.values():
            return r
        if r in ('owner', 'superadmin'):
            return 'super_admin'
        if r == 'admin':
            return 'admin'
    if user.is_staff:
        return 'admin'
    return 'staff'

def get_user_module_permissions_map(user, org: Organization) -> Dict[str, Dict[str, bool]]:
    """
    Returns the effective dynamic permission matrix for all 15 modules.
    Priority:
      1. Super Admin: full access to all 15 modules and all actions.
      2. Database records in UserModulePermission.
      3. Fallback to default role baseline for unconfigured modules.
    """
    from .models import UserModulePermission

    role = get_user_role(user, org)
    
    # 1. Super Admin: Full unconditional permissions
    if is_super_admin(user, org):
        return {
            m: {'view': True, 'create': True, 'edit': True, 'delete': True, 'export': True, 'approve': True}
            for m in ALL_MODULE_KEYS
        }

    # 2. Start from default role baseline
    base_map = DEFAULT_ROLE_MODULE_ACTIONS.get(role, DEFAULT_ROLE_MODULE_ACTIONS['staff'])
    result: Dict[str, Dict[str, bool]] = {
        m: dict(base_map.get(m, {'view': False, 'create': False, 'edit': False, 'delete': False, 'export': False, 'approve': False}))
        for m in ALL_MODULE_KEYS
    }

    # 3. Apply custom dynamic overrides from database
    if user and user.is_authenticated:
        db_perms = UserModulePermission.objects.filter(organization=org, user=user)
        for p in db_perms:
            if p.module in result:
                result[p.module] = {
                    'view': p.can_view,
                    'create': p.can_create,
                    'edit': p.can_edit,
                    'delete': p.can_delete,
                    'export': p.can_export,
                    'approve': p.can_approve,
                }
                
    return result

def has_module_action(user, org: Organization, module: str, action: str = 'view') -> bool:
    """Checks whether user has a specific granular action permission on a module."""
    if not user or not user.is_authenticated:
        return False
    if is_super_admin(user, org):
        return True
    
    perm_map = get_user_module_permissions_map(user, org)
    mod_perms = perm_map.get(module, {})
    return bool(mod_perms.get(action, False))

def check_permission(user, org: Organization, permission: str) -> bool:
    """Checks string permission e.g. 'vehicles:view' or legacy permissions."""
    if not user or not user.is_authenticated:
        return False
    if is_super_admin(user, org):
        return True
        
    if ':' in permission:
        mod, act = permission.split(':', 1)
        # normalize actions e.g. manage -> create
        act_map = {'manage': 'create', 'view': 'view', 'edit': 'edit', 'delete': 'delete', 'dispatch': 'create'}
        action = act_map.get(act, act)
        return has_module_action(user, org, mod, action)
    return has_module_action(user, org, permission, 'view')

def can_admin_grant_permission(admin_user, org: Organization, module: str, action: str) -> bool:
    """
    Main Admin can only grant permissions they themselves possess.
    Super Admin can grant anything.
    """
    if is_super_admin(admin_user, org):
        return True
    if not is_main_admin(admin_user, org):
        return False
    # Main Admin must have the permission themselves
    return has_module_action(admin_user, org, module, action)

def can_admin_manage_target_user(admin_user, target_user, org: Organization) -> bool:
    """
    Rules:
      - Only Super Admin can manage or modify a Super Admin.
      - Main Admin cannot promote to Super Admin or modify a Super Admin.
      - Main Admin can manage standard employees within delegated authority.
    """
    if is_super_admin(admin_user, org):
        return True
    if not is_main_admin(admin_user, org):
        return False
    # Cannot touch Super Admins
    if is_super_admin(target_user, org):
        return False
    return True

# ─── FRONTEND PERMISSIONS PAYLOAD BUILDER ──────────────────────────────────────

def get_user_permissions_payload(user, org: Organization) -> dict:
    """
    Comprehensive payload returned to Next.js frontend on load & login.
    Contains profile, role, and exact 15-module permission map.
    """
    from .models import UserProfile, AccessRequest

    role = get_user_role(user, org)
    super_adm = is_super_admin(user, org)
    main_adm = is_main_admin(user, org)
    
    profile_data = {
        'designation': 'Operations Staff',
        'department': 'Operations',
        'employee_id': '',
        'phone': '',
        'status': 'Active',
    }
    
    if user and user.is_authenticated and hasattr(user, 'tasin_profile'):
        p = user.tasin_profile
        profile_data['designation'] = p.designation
        profile_data['department'] = p.department
        profile_data['employee_id'] = p.employee_id
        profile_data['phone'] = p.phone
        profile_data['status'] = p.status

    modules_map = get_user_module_permissions_map(user, org)
    allowed_modules = [m for m, acts in modules_map.items() if acts.get('view', False)]
    
    # Flattened permission tokens for Can component
    flat_permissions = [
        f"{m}:{act}"
        for m, acts in modules_map.items()
        for act, allowed in acts.items()
        if allowed
    ]
    
    # Pending requests count
    pending_requests_count = 0
    if super_adm or main_adm:
        pending_requests_count = AccessRequest.objects.filter(organization=org, status='Pending').count()
    elif user and user.is_authenticated:
        pending_requests_count = AccessRequest.objects.filter(organization=org, user=user, status='Pending').count()

    return {
        'username': user.username if user and user.is_authenticated else 'guest',
        'name': user.get_full_name() or user.username if user and user.is_authenticated else 'Guest User',
        'email': user.email if user and user.is_authenticated else '',
        'role': role,
        'role_title': 'Super Admin' if super_adm else ('Main Admin' if main_adm else role.replace('_', ' ').title()),
        'is_super_admin': super_adm,
        'is_main_admin': main_adm,
        'profile': profile_data,
        'modules': modules_map,
        'allowed_modules': allowed_modules,
        'permissions': flat_permissions,
        'pending_requests_count': pending_requests_count,
        'all_modules_meta': MODULE_REGISTRY,
        
        # Legacy convenience flags
        'can_view_finance': modules_map.get('accounts', {}).get('view', False) or modules_map.get('expenses', {}).get('view', False),
        'can_manage_vehicles': modules_map.get('vehicles', {}).get('create', False),
        'can_manage_drivers': modules_map.get('drivers', {}).get('create', False),
        'can_manage_finances': modules_map.get('accounts', {}).get('create', False),
        'can_manage_users': super_adm or main_adm,
        'can_backup_database': super_adm,
        'can_custom_report': modules_map.get('reports', {}).get('create', False),
        'can_dispatch_duty': modules_map.get('duties', {}).get('create', False),
    }
