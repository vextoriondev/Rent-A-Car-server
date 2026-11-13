import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from rentals.models import Organization, Membership, UserProfile, UserModulePermission, AccessRequest, SystemAlert, AuditEvent
from rentals.rbac import (
    MODULE_REGISTRY, ALL_MODULE_KEYS, is_super_admin, is_main_admin,
    get_user_role, get_user_module_permissions_map, has_module_action,
    can_admin_grant_permission, can_admin_manage_target_user,
    get_user_permissions_payload
)

User = get_user_model()

def run_tests():
    print("==================================================")
    print("RUNNING TASIN ENTERPRISE DYNAMIC RBAC VERIFICATION")
    print("==================================================")

    # 1. Organization
    org = Organization.objects.first()
    if not org:
        org = Organization.objects.create(name="TASIN ENTERPRISE")
    print(f"[OK] Organization: {org.name} (ID: {org.pk})")

    # 2. Super Admin User
    admin_user, _ = User.objects.get_or_create(
        username="tasin_superadmin_test",
        defaults={"email": "superadmin@tasin.com", "is_superuser": True, "is_staff": True}
    )
    admin_user.set_password("AdminPass123!")
    admin_user.is_superuser = True
    admin_user.save()
    Membership.objects.update_or_create(
        organization=org,
        user=admin_user,
        defaults={"role": "admin"}
    )
    UserProfile.objects.update_or_create(
        user=admin_user,
        defaults={
            "organization": org,
            "designation": "Managing Director",
            "department": "Executive",
            "is_main_admin": False,
            "can_manage_users": True,
            "can_approve_requests": True
        }
    )
    assert is_super_admin(admin_user, org) is True, "Super admin detection failed"
    admin_payload = get_user_permissions_payload(admin_user, org)
    assert len(admin_payload['allowed_modules']) == 15, f"Super admin should have all 15 modules, got {len(admin_payload['allowed_modules'])}"
    print("[PASS] 1. Super Admin authority & full 15-module access verified.")

    # 3. Main Admin User (Delegated)
    main_admin_user, _ = User.objects.get_or_create(
        username="tasin_mainadmin_test",
        defaults={"email": "mainadmin@tasin.com", "is_superuser": False, "is_staff": False}
    )
    main_admin_user.save()
    Membership.objects.update_or_create(
        organization=org,
        user=main_admin_user,
        defaults={"role": "admin"}
    )
    UserProfile.objects.update_or_create(
        user=main_admin_user,
        defaults={
            "organization": org,
            "designation": "General Manager",
            "department": "Operations",
            "is_main_admin": True,
            "can_manage_users": True,
            "can_approve_requests": True
        }
    )
    assert is_main_admin(main_admin_user, org) is True, "Main admin detection failed"
    assert is_super_admin(main_admin_user, org) is False, "Main admin should not be super admin"
    print("[PASS] 2. Main Admin role & delegation authority verified.")

    # 4. Standard Employee User (Restricted to Duties only initially)
    employee, _ = User.objects.get_or_create(
        username="tasin_driver_mgr_test",
        defaults={"email": "drivermgr@tasin.com", "is_superuser": False}
    )
    employee.save()
    Membership.objects.update_or_create(
        organization=org,
        user=employee,
        defaults={"role": "staff"}
    )
    emp_profile, _ = UserProfile.objects.update_or_create(
        user=employee,
        defaults={
            "organization": org,
            "designation": "Duty Coordinator",
            "department": "Fleet Operations",
            "is_main_admin": False,
            "status": "Active"
        }
    )
    # Grant ONLY duties and vehicle_search
    UserModulePermission.objects.filter(organization=org, user=employee).delete()
    UserModulePermission.objects.create(
        organization=org,
        user=employee,
        module="duties",
        can_view=True,
        can_create=True,
        can_edit=True,
        can_delete=False
    )
    UserModulePermission.objects.create(
        organization=org,
        user=employee,
        module="vehicle_search",
        can_view=True,
        can_create=False,
        can_edit=False,
        can_delete=False
    )
    
    emp_map = get_user_module_permissions_map(employee, org)
    assert emp_map["duties"]["view"] is True, "Employee should have duties view"
    assert emp_map["duties"]["create"] is True, "Employee should have duties create"
    assert emp_map["duties"]["delete"] is False, "Employee should NOT have duties delete"
    assert emp_map["accounts"]["view"] is False, "Employee should NOT have accounts view"
    assert emp_map["settings"]["view"] is False, "Employee should NOT have settings view"
    print("[PASS] 3. Employee dynamic module & granular action isolation verified.")

    # 5. Access Request Creation (Employee requests 'accounts')
    AccessRequest.objects.filter(organization=org, user=employee, requested_module="accounts").delete()
    req = AccessRequest.objects.create(
        organization=org,
        user=employee,
        requested_module="accounts",
        can_view=True,
        can_create=False,
        can_edit=False,
        can_delete=False,
        reason="Need to view invoice ledgers for weekly client reconciliation"
    )
    assert req.status == "Pending", "New access request must start in Pending state"
    
    # Verify employee STILL DOES NOT have accounts access before approval!
    emp_map_before = get_user_module_permissions_map(employee, org)
    assert emp_map_before["accounts"]["view"] is False, "Pending access request must NEVER prematurely grant access"
    print("[PASS] 4. Access request created; strict non-grant before approval verified.")

    # 6. Admin Approves Request
    from rentals.api.tasin import approve_access_request
    class DummyReq:
        user = admin_user
        headers = {}
    
    res = approve_access_request(DummyReq(), org.pk, req.id)
    assert res["success"] is True, "Approval must return success"
    req.refresh_from_db()
    assert req.status == "Approved", "Request status must be updated to Approved"
    assert req.reviewed_by == admin_user, "Reviewed by must be recorded"
    
    # Verify employee NOW has accounts view access!
    emp_map_after = get_user_module_permissions_map(employee, org)
    assert emp_map_after["accounts"]["view"] is True, "Employee should have accounts view after approval"
    assert emp_map_after["accounts"]["create"] is False, "Employee should NOT have accounts create"
    print("[PASS] 5. Admin approval workflow atomically granted dynamic DB permissions.")

    # 7. Employee Requests Another Module ('owners') & Admin Rejects with Reason
    AccessRequest.objects.filter(organization=org, user=employee, requested_module="owners").delete()
    req2 = AccessRequest.objects.create(
        organization=org,
        user=employee,
        requested_module="owners",
        can_view=True,
        can_create=True,
        reason="I want to see vehicle owners contracts"
    )
    from rentals.api.tasin import reject_access_request, AccessRequestRejectIn
    rej_in = AccessRequestRejectIn(rejection_reason="Restricted to Executive Management only")
    res_rej = reject_access_request(DummyReq(), org.pk, req2.id, rej_in)
    assert res_rej["success"] is True, "Rejection must return success"
    req2.refresh_from_db()
    assert req2.status == "Rejected", "Status must be Rejected"
    assert req2.rejection_reason == "Restricted to Executive Management only", "Rejection reason recorded"
    
    emp_map_rej = get_user_module_permissions_map(employee, org)
    assert emp_map_rej["owners"]["view"] is False, "Employee must NOT have owners view after rejection"
    # Ensure previously approved 'accounts' is STILL intact!
    assert emp_map_rej["accounts"]["view"] is True, "Previously approved accounts permission must remain intact"
    print("[PASS] 6. Rejection workflow preserved existing permissions and recorded reason.")

    # 8. Delegation Safety Check
    # Main Admin cannot modify Super Admin
    assert can_admin_manage_target_user(main_admin_user, admin_user, org) is False, "Main Admin must not manage Super Admin"
    # Super Admin can manage anyone
    assert can_admin_manage_target_user(admin_user, main_admin_user, org) is True, "Super Admin can manage Main Admin"
    assert can_admin_manage_target_user(admin_user, employee, org) is True, "Super Admin can manage Employee"
    print("[PASS] 7. Hierarchical delegation security boundaries verified.")

    print("==================================================")
    print("ALL 7 DYNAMIC RBAC CORE WORKFLOW TESTS PASSED!")
    print("==================================================")

if __name__ == '__main__':
    run_tests()
