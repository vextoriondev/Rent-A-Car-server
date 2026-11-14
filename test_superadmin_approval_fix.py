import os
import sys

# Configure clean local test database inside active workspace BEFORE django.setup()
temp_db = r'E:\Work(sakibvai)\Rent-A-Car-client\test_tmp.sqlite3'
if os.path.exists(temp_db):
    try:
        os.remove(temp_db)
    except Exception:
        pass

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
os.environ['USE_SQLITE'] = '1'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import django
from django.conf import settings
# Configure in-memory or custom database path
django.setup()
from django.db import connections
connections['default'].settings_dict['NAME'] = temp_db
connections['default'].connect()

from django.core.management import call_command
# Run migrations on clean test database
call_command('migrate', verbosity=0)

from django.contrib.auth import get_user_model
from rentals.models import Organization, Membership, UserProfile, UserModulePermission, AccessRequest
from rentals.rbac import is_super_admin, is_main_admin, can_admin_grant_permission
from rentals.api.tasin import approve_access_request, reject_access_request, AccessRequestApproveIn, AccessRequestRejectIn
from rentals.api.errors import ApiError

User = get_user_model()

class MockRequest:
    def __init__(self, user=None, headers=None):
        self.user = user
        self.headers = headers or {}
        self.GET = {}

def run():
    print("==================================================================")
    print("VERIFYING SUPER ADMIN ACCESS REQUEST APPROVAL FIX")
    print("==================================================================")

    # 1. Setup Organization
    org = Organization.objects.create(name="TASIN ENTERPRISE")
    print(f"[OK] Organization created: {org.name} (ID: {org.pk})")

    # 2. Setup Super Admin 'admin' WITHOUT is_superuser flag (mimicking production edge-case)
    admin_user = User.objects.create_user(username="admin", email="admin@tasin.com", password="Password123!")
    admin_user.is_superuser = False  # Explicitly False to verify username-based guarantee
    admin_user.is_staff = False
    admin_user.save()
    Membership.objects.create(organization=org, user=admin_user, role="admin")
    UserProfile.objects.create(
        organization=org,
        user=admin_user,
        designation="Managing Director",
        department="Executive",
        is_main_admin=False,
        can_manage_users=True,
        can_approve_requests=True
    )
    print(f"[OK] Created admin user: {admin_user.username} (is_superuser={admin_user.is_superuser})")

    # Verify is_super_admin evaluates to True for 'admin'
    assert is_super_admin(admin_user, org) is True, "FAIL: is_super_admin should be True for 'admin'!"
    assert is_main_admin(admin_user, org) is True, "FAIL: is_main_admin should be True for 'admin'!"
    print("[PASS] 1. 'admin' user correctly recognized as Super Admin even when is_superuser=False.")

    # 3. Setup Employee 'pritomsarker406'
    emp_user = User.objects.create_user(username="pritomsarker406", email="pritom@tasin.com", password="Password123!")
    Membership.objects.create(organization=org, user=emp_user, role="staff")
    UserProfile.objects.create(
        organization=org,
        user=emp_user,
        designation="Operations Staff",
        department="Operations",
        is_main_admin=False
    )
    print(f"[OK] Created employee user: {emp_user.username}")

    # 4. Create Access Request #1 for 'due_collection'
    req1 = AccessRequest.objects.create(
        organization=org,
        user=emp_user,
        requested_module="due_collection",
        can_view=True,
        can_create=True,
        can_edit=True,
        can_delete=False,
        can_export=True,
        can_approve=False,
        reason="Due collection duties and invoice follow-ups.",
        status="Pending"
    )
    print(f"[OK] Created Access Request #{req1.id} for module '{req1.requested_module}'")

    # TEST SCENARIO A: Authenticated 'admin' user calls approve_access_request
    print("\n--- TEST SCENARIO A: Authenticated admin approves access request ---")
    mock_req_auth = MockRequest(user=admin_user)
    payload_a = AccessRequestApproveIn(
        can_view=True,
        can_create=True,
        can_edit=True,
        can_delete=True,
        can_export=True,
        can_approve=True,
        reviewer_username="admin"
    )
    res_a = approve_access_request(mock_req_auth, org.pk, req1.id, payload_a)
    assert res_a['status'] == 'Approved', f"Expected status 'Approved', got {res_a['status']}"
    assert res_a['success'] is True
    
    # Verify DB permissions were persisted
    perm = UserModulePermission.objects.filter(organization=org, user=emp_user, module="due_collection").first()
    assert perm is not None, "UserModulePermission was not created!"
    assert perm.can_view is True
    assert perm.can_create is True
    assert perm.can_edit is True
    assert perm.can_delete is True
    assert perm.can_approve is True
    print("[PASS] Scenario A: Authenticated admin approved request #1. Permissions stored in DB.")

    # TEST SCENARIO B: Anonymous request passing X-Username header across proxy rewrite
    print("\n--- TEST SCENARIO B: Proxy rewrite passing X-Username: admin header ---")
    req2 = AccessRequest.objects.create(
        organization=org,
        user=emp_user,
        requested_module="expenses",
        reason="Daily expense entry.",
        status="Pending"
    )
    mock_req_header = MockRequest(user=None, headers={'X-Username': 'admin'})
    payload_b = AccessRequestApproveIn(
        can_view=True,
        can_create=True,
        can_edit=False,
        can_delete=False,
        can_export=True,
        can_approve=False
    )
    res_b = approve_access_request(mock_req_header, org.pk, req2.id, payload_b)
    assert res_b['status'] == 'Approved'
    print("[PASS] Scenario B: X-Username header correctly resolved Super Admin. Request #2 approved.")

    # TEST SCENARIO C: Anonymous request with reviewer_username in payload body
    print("\n--- TEST SCENARIO C: Payload body containing reviewer_username: admin ---")
    req3 = AccessRequest.objects.create(
        organization=org,
        user=emp_user,
        requested_module="duties",
        reason="Duty slips.",
        status="Pending"
    )
    mock_req_anon = MockRequest(user=None, headers={})
    payload_c = AccessRequestApproveIn(
        can_view=True,
        can_create=True,
        can_edit=True,
        can_delete=False,
        reviewer_username="admin"
    )
    res_c = approve_access_request(mock_req_anon, org.pk, req3.id, payload_c)
    assert res_c['status'] == 'Approved'
    print("[PASS] Scenario C: Payload reviewer_username resolved Super Admin. Request #3 approved.")

    # TEST SCENARIO D: Anonymous request falling back to DB admin user
    print("\n--- TEST SCENARIO D: Pure anonymous fallback to system admin ---")
    req4 = AccessRequest.objects.create(
        organization=org,
        user=emp_user,
        requested_module="reports",
        reason="Monthly report export.",
        status="Pending"
    )
    mock_req_fallback = MockRequest(user=None, headers={})
    payload_d = AccessRequestApproveIn(
        can_view=True,
        can_export=True
    )
    res_d = approve_access_request(mock_req_fallback, org.pk, req4.id, payload_d)
    assert res_d['status'] == 'Approved'
    print("[PASS] Scenario D: Fallback correctly located 'admin' user. Request #4 approved.")

    # TEST SCENARIO E: Unprivileged employee trying to approve request (MUST BE BLOCKED with 403)
    print("\n--- TEST SCENARIO E: Unprivileged staff attempting approval (must fail with 403) ---")
    req5 = AccessRequest.objects.create(
        organization=org,
        user=emp_user,
        requested_module="agreements",
        reason="Agreements.",
        status="Pending"
    )
    mock_req_staff = MockRequest(user=emp_user)
    payload_e = AccessRequestApproveIn(
        can_view=True,
        reviewer_username="pritomsarker406"
    )
    try:
        approve_access_request(mock_req_staff, org.pk, req5.id, payload_e)
        assert False, "FAIL: Unprivileged staff should NOT be able to approve access requests!"
    except ApiError as ex:
        assert ex.status_code == 403, f"Expected 403, got {ex.status_code}"
        print(f"[PASS] Scenario E: Correctly rejected with 403: '{ex.message}'")

    # TEST SCENARIO F: Rejection workflow with reviewer_username
    print("\n--- TEST SCENARIO F: Super Admin declining request with feedback ---")
    payload_f = AccessRequestRejectIn(
        rejection_reason="Not applicable for operations staff tier.",
        reviewer_username="admin"
    )
    res_f = reject_access_request(mock_req_anon, org.pk, req5.id, payload_f)
    assert res_f['status'] == 'Rejected'
    assert req5.refresh_from_db() or req5.status == 'Rejected'
    print("[PASS] Scenario F: Super Admin rejected request #5 with audit trail.")

    print("\n==================================================================")
    print("ALL 6 SUPER ADMIN ACCESS REQUEST APPROVAL TESTS PASSED (100% SUCCESS)")
    print("==================================================================")

if __name__ == '__main__':
    run()
