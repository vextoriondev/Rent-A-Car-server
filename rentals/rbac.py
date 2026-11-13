"""
TASIN ENTERPRISE — Granular Role-Based Access Control (RBAC)
Defines roles, module-level permissions, and decorators.
Roles:
  1. super_admin: Full access to all modules, users, backups, and settings.
  2. admin: Full business operations, view all reports, financial transactions.
  3. accountant: Financial access (Invoices, Billing, Collections, Payments, Expenses, Accounts, Ledger, Financial Reports, Profitability). Cannot add/delete vehicles or assign drivers.
  4. operations_manager: Operational access (Vehicles, Drivers, Duties, Attendance, Maintenance, Garages, Vehicle Search, Spare Parts, Trip Dispatch). Cannot access P&L, ledger balances, or manage user accounts.
  5. staff: Basic view access to assigned duties, submit duty status, search vehicles.
"""

from functools import wraps
from typing import List, Optional
from django.http import HttpRequest
from ninja.errors import HttpError
from .models import Membership, Organization

ROLES = {
    'SUPER_ADMIN': 'super_admin',
    'ADMIN': 'admin',
    'ACCOUNTANT': 'accountant',
    'OPERATIONS_MANAGER': 'operations_manager',
    'STAFF': 'staff',
}

ROLE_PERMISSIONS = {
    'super_admin': [
        'dashboard:view', 'dashboard:financial',
        'vehicles:view', 'vehicles:manage',
        'owners:view', 'owners:manage',
        'drivers:view', 'drivers:manage', 'drivers:attendance',
        'clients:view', 'clients:manage', 'invoices:manage',
        'duties:view', 'duties:manage', 'duties:dispatch',
        'expenses:view', 'expenses:manage',
        'maintenance:view', 'maintenance:manage', 'inventory:manage',
        'agreements:view', 'agreements:manage',
        'accounts:view', 'accounts:manage', 'bank_reconciliation:manage',
        'due_collection:view', 'due_collection:manage',
        'reports:view', 'reports:custom',
        'notifications:view', 'notifications:manage',
        'settings:view', 'settings:manage', 'users:manage', 'backup:manage'
    ],
    'admin': [
        'dashboard:view', 'dashboard:financial',
        'vehicles:view', 'vehicles:manage',
        'owners:view', 'owners:manage',
        'drivers:view', 'drivers:manage', 'drivers:attendance',
        'clients:view', 'clients:manage', 'invoices:manage',
        'duties:view', 'duties:manage', 'duties:dispatch',
        'expenses:view', 'expenses:manage',
        'maintenance:view', 'maintenance:manage', 'inventory:manage',
        'agreements:view', 'agreements:manage',
        'accounts:view', 'accounts:manage', 'bank_reconciliation:manage',
        'due_collection:view', 'due_collection:manage',
        'reports:view', 'reports:custom',
        'notifications:view', 'notifications:manage',
        'settings:view', 'settings:manage', 'users:manage'
    ],
    'accountant': [
        'dashboard:view', 'dashboard:financial',
        'vehicles:view',
        'owners:view', 'owners:manage',
        'drivers:view',
        'clients:view', 'clients:manage', 'invoices:manage',
        'duties:view',
        'expenses:view', 'expenses:manage',
        'maintenance:view',
        'agreements:view',
        'accounts:view', 'accounts:manage', 'bank_reconciliation:manage',
        'due_collection:view', 'due_collection:manage',
        'reports:view', 'reports:custom',
        'notifications:view'
    ],
    'operations_manager': [
        'dashboard:view',
        'vehicles:view', 'vehicles:manage',
        'owners:view',
        'drivers:view', 'drivers:manage', 'drivers:attendance',
        'clients:view',
        'duties:view', 'duties:manage', 'duties:dispatch',
        'expenses:view', 'expenses:manage',
        'maintenance:view', 'maintenance:manage', 'inventory:manage',
        'agreements:view',
        'due_collection:view',
        'reports:view',
        'notifications:view', 'notifications:manage'
    ],
    'staff': [
        'dashboard:view',
        'vehicles:view',
        'drivers:view',
        'duties:view', 'duties:manage',
        'notifications:view'
    ]
}

def get_user_role(user, org: Organization) -> str:
    """Returns normalized role string for user in org."""
    if not user or not user.is_authenticated:
        return 'staff'
    if user.is_superuser:
        return 'super_admin'
    
    membership = Membership.objects.filter(user=user, organization=org).first()
    if membership:
        r = (membership.role or 'staff').lower().replace('-', '_').replace(' ', '_')
        if r in ROLE_PERMISSIONS:
            return r
        if r == 'admin':
            return 'admin'
    if user.is_staff:
        return 'admin'
    return 'staff'

def check_permission(user, org: Organization, permission: str) -> bool:
    """Checks if user has a specific permission in org."""
    role = get_user_role(user, org)
    perms = ROLE_PERMISSIONS.get(role, [])
    return permission in perms

def get_user_permissions_payload(user, org: Organization) -> dict:
    """Returns permission summary for frontend UI consumption."""
    role = get_user_role(user, org)
    perms = ROLE_PERMISSIONS.get(role, [])
    return {
        'username': user.username if user and user.is_authenticated else 'guest',
        'role': role,
        'role_title': role.replace('_', ' ').title(),
        'permissions': perms,
        'can_view_finance': 'dashboard:financial' in perms,
        'can_manage_vehicles': 'vehicles:manage' in perms,
        'can_manage_drivers': 'drivers:manage' in perms,
        'can_manage_finances': 'accounts:manage' in perms,
        'can_manage_users': 'users:manage' in perms,
        'can_backup_database': 'backup:manage' in perms,
        'can_custom_report': 'reports:custom' in perms,
        'can_dispatch_duty': 'duties:dispatch' in perms,
    }

