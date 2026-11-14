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
from rentals.api.tasin import (
    create_system_user, update_user_profile, list_access_requests,
    get_access_requests_summary, get_access_request_review_details,
    create_access_request, approve_access_request, reject_access_request,
    UserCreateIn, UserProfileUpdateIn, ModuleActionItemIn,
    AccessRequestCreateIn, AccessRequestApproveIn, AccessRequestRejectIn
)

User = get_user_model()

class MockRequest:
    def __init__(self, user=None, headers=None):
        self.user = user
        self.headers = headers or {}

def run_tests():
    print("==================================================================")
    print("RUNNING TASIN ENTERPRISE RBAC & ACCESS REQUEST VERIFICATION SUITE")
    print("==================================================================")

    # 1. Organization
    org = Organization.objects.first()
    if not org:
        org = Organization.objects.create(name="TASIN ENTERPRISE")
    print(f"[OK] Organization: {org.name} (ID: {org.pk})")

    # 2. Super Admin 1
    sa1, _ = User.objects.get_or_create(username="sa_tasin_1", defaults={"email": "sa1@tasin.com", "is_superuser": True, "is_staff": True})
    sa1.is_superuser = True
    sa1.save()
    Membership.objects.update_or_create(organization=org, user=sa1, defaults={"role": "super_admin"})
    UserProfile.objects.update_or_create(user=sa1, defaults={"organization": org, "designation": "Managing Director", "department": "Executive", "is_main_admin": False})

    # 3. Super Admin 2 (Multi-Super Admin support verification)
    sa2, _ = User.objects.get_or_create(username="sa_tasin_2", defaults={"email": "sa2@tasin.com", "is_superuser": True, "is_staff": True})
    sa2.is_superuser = True
    sa2.save()
    Membership.objects.update_or_create(organization=org, user=sa2, defaults={"role": "super_admin"})
    UserProfile.objects.update_or_create(user=sa2, defaults={"organization": org, "designation": "Director of Technology", "department": "Executive", "is_main_admin": False})

    req_sa1 = MockRequest(user=sa1)
    req_sa2 = MockRequest(user=sa2)

    # TEST A: CREATE USER WITH INTEGRATED 15-MODULE PERMISSION MATRIX
    print("\n--- TEST A: CREATE USER WITH INTEGRATED 15-MODULE PERMISSION MATRIX ---")
    emp_uname = "emp_fleet_test1"
    User.objects.filter(username=emp_uname).delete()

    # Super Admin creates an employee with specific permissions for 4 modules:
    # 1. vehicles: View=True, Create=True, Edit=True, Delete=False, Export=True, Approve=False
    # 2. duties: View=True, Create=True, Edit=True, Delete=False, Export=True, Approve=True
    # 3. expenses: View=True, Create=True, Edit=False, Delete=False, Export=False, Approve=False
    # 4. vehicle_search: View=True, Create=False, Edit=False, Delete=False, Export=False, Approve=False
    # Remaining 11 modules: View=False (no access)
    
    custom_perms = {
        'vehicles': ModuleActionItemIn(can_view=True, can_create=True, can_edit=True, can_delete=False, can_export=True, can_approve=False),
        'duties': ModuleActionItemIn(can_view=True, can_create=True, can_edit=True, can_delete=False, can_export=True, can_approve=True),
        'expenses': ModuleActionItemIn(can_view=True, can_create=True, can_edit=False, can_delete=False, can_export=False, can_approve=False),
        'vehicle_search': ModuleActionItemIn(can_view=True, can_create=False, can_edit=False, can_delete=False, can_export=False, can_approve=False),
    }

    create_payload = UserCreateIn(
        username=emp_uname,
        password="FleetPass2026!#",
        email="emp_fleet@tasinenterprise.com.bd",
        name="Tareq Hasan",
        phone="+880 1711-998877",
        role="staff",
        designation="Senior Dispatcher",
        department="Fleet Logistics",
        employee_id="EMP-9081",
        status="Active",
        module_permissions=custom_perms
    )

    res_user = create_system_user(req_sa1, org.pk, create_payload)
    assert res_user["success"] is True, "User creation must return success"
    created_user = User.objects.get(username=emp_uname)
    assert created_user.check_password("FleetPass2026!#"), "Password must be securely hashed and valid"
    assert created_user.first_name == "Tareq", "First name must be set"
    assert created_user.last_name == "Hasan", "Last name must be set"

    # Verify UserProfile
    profile = created_user.tasin_profile
    assert profile.designation == "Senior Dispatcher"
    assert profile.department == "Fleet Logistics"
    assert profile.employee_id == "EMP-9081"
    assert profile.status == "Active"

    # Verify Database Module Permissions
    emp_map = get_user_module_permissions_map(created_user, org)
    
    # 4 selected modules MUST be enabled exactly as specified
    assert emp_map["vehicles"]["view"] is True, "vehicles view must be True"
    assert emp_map["vehicles"]["create"] is True, "vehicles create must be True"
    assert emp_map["vehicles"]["delete"] is False, "vehicles delete must be False"
    assert emp_map["vehicles"]["export"] is True, "vehicles export must be True"

    assert emp_map["duties"]["view"] is True, "duties view must be True"
    assert emp_map["duties"]["approve"] is True, "duties approve must be True"

    assert emp_map["expenses"]["view"] is True, "expenses view must be True"
    assert emp_map["expenses"]["edit"] is False, "expenses edit must be False"

    assert emp_map["vehicle_search"]["view"] is True, "vehicle_search view must be True"
    assert emp_map["vehicle_search"]["create"] is False, "vehicle_search create must be False"

    # Unselected modules MUST have no access (view=False)
    assert emp_map["accounts"]["view"] is False, "accounts view must be False"
    assert emp_map["owners"]["view"] is False, "owners view must be False"
    assert emp_map["settings"]["view"] is False, "settings view must be False"
    assert emp_map["reports"]["view"] is False, "reports view must be False"

    payload_emp = get_user_permissions_payload(created_user, org)
    assert set(payload_emp["allowed_modules"]) == {"vehicles", "duties", "expenses", "vehicle_search"}
    print(f"[PASS] User '{emp_uname}' created with 4 exact modules and custom actions atomically.")

    # TEST B: EDIT EXISTING USER PROFILE & PERMISSIONS TOGETHER
    print("\n--- TEST B: EDIT USER PROFILE & PERMISSIONS TOGETHER ---")
    update_perms = {
        **custom_perms,
        'reports': ModuleActionItemIn(can_view=True, can_create=False, can_edit=False, can_delete=False, can_export=True, can_approve=False)
    }
    update_payload = UserProfileUpdateIn(
        name="Tareq Hasan Majumder",
        designation="Lead Fleet Dispatcher",
        module_permissions=update_perms
    )
    res_up = update_user_profile(req_sa1, org.pk, created_user.pk, update_payload)
    assert res_up["success"] is True

    created_user.refresh_from_db()
    assert created_user.tasin_profile.designation == "Lead Fleet Dispatcher"
    emp_map_up = get_user_module_permissions_map(created_user, org)
    assert emp_map_up["reports"]["view"] is True, "reports should now be viewable after update"
    assert emp_map_up["reports"]["export"] is True, "reports export should be True"
    print("[PASS] User profile and module permissions updated together atomically.")

    # TEST C: EMPLOYEE ACCESS REQUEST ROUTING & SUPER ADMIN INBOX
    print("\n--- TEST C: EMPLOYEE ACCESS REQUEST & INBOX ROUTING ---")
    req_emp = MockRequest(user=created_user)
    AccessRequest.objects.filter(organization=org, user=created_user, requested_module="accounts").delete()

    ar_in = AccessRequestCreateIn(
        module="accounts",
        can_view=True,
        can_create=True,
        can_edit=False,
        can_delete=False,
        reason="Need access to reconcile client duty cash receipts with company bank accounts"
    )
    res_ar = create_access_request(req_emp, org.pk, ar_in)
    assert res_ar["success"] is True
    ar_id = res_ar["id"]
    print(f"[OK] Access request #{ar_id} submitted by '{created_user.username}'.")

    # Verify pending state: user STILL DOES NOT have accounts access!
    emp_map_pending = get_user_module_permissions_map(created_user, org)
    assert emp_map_pending["accounts"]["view"] is False, "Pending request must NEVER grant access prematurely"

    # Both Super Admins MUST see this pending request in their organization inbox
    inbox_sa1 = list_access_requests(req_sa1, org.pk, status="Pending")
    inbox_sa2 = list_access_requests(req_sa2, org.pk, status="Pending")
    assert any(r["id"] == ar_id for r in inbox_sa1), "Super Admin 1 must see pending request"
    assert any(r["id"] == ar_id for r in inbox_sa2), "Super Admin 2 must see pending request"

    # Verify Summary counts
    summary = get_access_requests_summary(req_sa1, org.pk)
    assert summary["pending"] >= 1, "Pending count must be at least 1"
    print(f"[PASS] Multi-Super Admin visibility confirmed. Pending requests: {summary['pending']}.")

    # TEST D: DETAILED REVIEW PAYLOAD FOR SUPER ADMIN
    print("\n--- TEST D: DETAILED REVIEW PAYLOAD ---")
    review_details = get_access_request_review_details(req_sa1, org.pk, ar_id)
    assert review_details["id"] == ar_id
    assert review_details["username"] == emp_uname
    assert review_details["requestedModule"] == "accounts"
    assert review_details["canView"] is True
    assert review_details["canCreate"] is True
    assert "duties" in review_details["currentGrantedModules"]
    print("[PASS] Super Admin review package contains complete employee context and permission history.")

    # TEST E: SUPER ADMIN 1 APPROVES WITH MODIFIED PERMISSIONS (Review & Modify)
    print("\n--- TEST E: APPROVE WITH MODIFIED PERMISSIONS ---")
    # Super Admin decides to grant View=True, but withhold Create=False
    mod_approve = AccessRequestApproveIn(can_view=True, can_create=False)
    res_app = approve_access_request(req_sa1, org.pk, ar_id, mod_approve)
    assert res_app["success"] is True

    # Verify atomic DB permission assignment
    emp_map_after = get_user_module_permissions_map(created_user, org)
    assert emp_map_after["accounts"]["view"] is True, "Employee must now have accounts view"
    assert emp_map_after["accounts"]["create"] is False, "Employee must NOT have accounts create (modified)"

    # Verify Super Admin 2 now sees it as Approved (no duplicate approval)
    req_obj = AccessRequest.objects.get(pk=ar_id)
    assert req_obj.status == "Approved"
    assert req_obj.reviewed_by == sa1

    # Attempt duplicate approval from Super Admin 2: must be BLOCKED!
    try:
        approve_access_request(req_sa2, org.pk, ar_id)
        assert False, "Duplicate approval should have been blocked"
    except Exception as e:
        assert "already been processed" in str(e), f"Expected already processed error, got: {e}"
        print("[PASS] Duplicate processing blocked across multiple Super Admins.")

    # TEST F: ACCESS REQUEST REJECTION PRESERVES PERMISSIONS
    print("\n--- TEST F: ACCESS REQUEST REJECTION WORKFLOW ---")
    AccessRequest.objects.filter(organization=org, user=created_user, requested_module="agreements").delete()
    ar_rej_in = AccessRequestCreateIn(
        module="agreements",
        can_view=True,
        can_create=True,
        reason="Need access to vehicle agreements"
    )
    res_rej_req = create_access_request(req_emp, org.pk, ar_rej_in)
    ar_rej_id = res_rej_req["id"]

    rej_payload = AccessRequestRejectIn(rejection_reason="Executive approval required for legal documents")
    res_rej = reject_access_request(req_sa2, org.pk, ar_rej_id, rej_payload)
    assert res_rej["success"] is True

    # Verify DB permissions: agreements is NOT granted, while accounts and duties remain INTACT!
    emp_map_final = get_user_module_permissions_map(created_user, org)
    assert emp_map_final["agreements"]["view"] is False, "agreements must NOT be viewable"
    assert emp_map_final["accounts"]["view"] is True, "previously approved accounts MUST remain viewable"
    assert emp_map_final["duties"]["view"] is True, "previously approved duties MUST remain viewable"
    print("[PASS] Rejection workflow preserved existing permissions and recorded decline reason.")

    print("\n==================================================================")
    print("ALL RBAC & ACCESS REQUEST WORKFLOW VERIFICATION SUITES PASSED (100%)")
    print("==================================================================")

if __name__ == '__main__':
    run_tests()
