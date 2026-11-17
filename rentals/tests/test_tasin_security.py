"""Security regression tests for the Tasin routers: authentication, organization isolation, RBAC, identity spoofing."""
import re
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from rentals.api import tasin, finance
from rentals.api.tasin_auth import required_permission, ORG_PATH
from rentals.models import Organization, Branch, Membership, Party, AccessRequest

User = get_user_model()


# These three Tasin routes are shadowed by identically-shaped routes in other routers and answer 405 for everyone (pre-existing, see REMAINING_ISSUES).
SHADOWED = {('POST', '/api/v1/orgs/1/reports/custom-builder'), ('POST', '/api/v1/orgs/1/documents/upload'), ('GET', '/api/v1/orgs/1/documents')}


def routes(router):
    """(method, concrete path) for every reachable operation of a router, with {params} replaced by 1."""
    out = []
    for path, view in router.path_operations.items():
        for op in view.operations:
            for method in op.methods:
                item = (method, '/api/v1/' + re.sub(r'\{[^}]+\}', '1', path).lstrip('/'))
                if item not in SHADOWED:
                    out.append(item)
    return out


class SecurityBase(TestCase):
    maxDiff = None
    def setUp(self):
        self.org = Organization.objects.create(name='A')
        self.org_b = Organization.objects.create(name='B')
        self.branch = Branch.objects.create(organization=self.org, name='Main')
        Branch.objects.create(organization=self.org_b, name='Main')
        self.staff = User.objects.create_user('staffer', password='x')
        Membership.objects.create(organization=self.org, user=self.staff, role='viewer', branch=self.branch)
        self.viewer_client = Client()
        self.viewer_client.force_login(self.staff)
        self.admin = User.objects.create_user('admin', password='x')
        Membership.objects.create(organization=self.org, user=self.admin, role='admin', branch=self.branch)
        self.admin_client = Client()
        self.admin_client.force_login(self.admin)
        self.foreign = User.objects.create_user('foreign_admin', password='x')   # admin of ANOTHER organization
        Membership.objects.create(organization=self.org_b, user=self.foreign, role='admin')
        self.foreign_client = Client()
        self.foreign_client.force_login(self.foreign)

    def org_path(self, path):
        return path.replace('/orgs/1/', f'/orgs/{self.org.pk}/')

    def call(self, client, method, path):
        if method == 'GET':
            return client.get(path)
        return getattr(client, method.lower())(path, data='{}', content_type='application/json')


class UnauthenticatedTests(SecurityBase):
    def test_every_tasin_and_finance_route_requires_login_except_health(self):
        anon = Client()
        bad = []
        for router in (tasin.router, finance.router):
            for method, path in routes(router):
                if path.endswith('/health'):
                    continue
                r = self.call(anon, method, self.org_path(path))
                if r.status_code != 401:
                    bad.append((method, path, r.status_code))
        self.assertEqual(bad, [])

    def test_health_is_public_and_leaks_nothing(self):
        r = Client().get('/api/v1/health')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()), {'status', 'message', 'version', 'time'})

    def test_connection_status_and_overview_need_login(self):
        for p in ('/api/v1/connection/status', '/api/v1/tasin/overview'):
            self.assertEqual(Client().get(p).status_code, 401)

    def test_spoofed_identity_headers_do_not_authenticate(self):
        r = Client().get(f'/api/v1/orgs/{self.org.pk}/tasin-owners', HTTP_X_USERNAME='admin', HTTP_X_USER_ID=str(self.admin.pk))
        self.assertEqual(r.status_code, 401)


class OrganizationIsolationTests(SecurityBase):
    def test_member_of_other_org_is_rejected_everywhere(self):
        bad = []
        for router in (tasin.router, finance.router):
            for method, path in routes(router):
                if path.endswith('/health') or '/orgs/' not in path:
                    continue
                r = self.call(self.foreign_client, method, self.org_path(path))
                if r.status_code != 403:
                    bad.append((method, path, r.status_code))
        self.assertEqual(bad, [])

    def test_unknown_organization_is_404_not_a_fallback(self):
        r = self.admin_client.get('/api/v1/orgs/9999/tasin-owners')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Organization.objects.count(), 2)  # nothing auto-created

    def test_own_org_data_only(self):
        Party.objects.create(organization=self.org_b, kind='owner', name='B Owner')
        names = [o['name'] for o in self.admin_client.get(f'/api/v1/orgs/{self.org.pk}/tasin-owners').json()]
        self.assertNotIn('B Owner', names)


class RbacTests(SecurityBase):
    def test_mutations_are_denied_exactly_where_rbac_denies(self):
        """For every mutating route: 403 when the role lacks the module action, anything but 401/403 when it has it."""
        from rentals.rbac import has_module_action
        checked = denied = 0
        for router in (tasin.router, finance.router):
            for method, path in routes(router):
                if method == 'GET' or path.endswith('/health') or '/orgs/' not in path:
                    continue
                p = self.org_path(path)
                need = required_permission(method, ORG_PATH.search(p).group(2))
                self.assertIsNotNone(need, path)
                module, action = need
                if module in ('*', '<party>'):
                    continue
                allowed = has_module_action(self.staff, self.org, module, action)
                r = self.call(self.viewer_client, method, p)
                checked += 1
                if allowed:
                    self.assertNotIn(r.status_code, (401, 403), (method, path))
                else:
                    denied += 1
                    self.assertEqual(r.status_code, 403, (method, path))
        self.assertGreater(denied, 40)
        self.assertGreater(checked, 60)

    def test_party_routes_follow_the_party_kind(self):
        owner = Party.objects.create(organization=self.org, kind='owner', name='O')
        driver = Party.objects.create(organization=self.org, kind='driver', name='D')
        # staff baseline: no owners module, but drivers view; creating on either is denied
        for party in (owner, driver):
            r = self.viewer_client.post(f'/api/v1/orgs/{self.org.pk}/parties/{party.pk}/opening-balance', data={'balance_type': 'payable', 'amount': '1'}, content_type='application/json')
            self.assertEqual(r.status_code, 403)
        r = self.viewer_client.post(f'/api/v1/orgs/{self.org.pk}/parties/99999/opening-balance', data={}, content_type='application/json')
        self.assertEqual(r.status_code, 404)

    def test_viewer_reads_never_crash(self):
        for method, path in routes(tasin.router):
            if method != 'GET' or path.endswith('/health') or '/orgs/' not in path:
                continue
            r = self.viewer_client.get(self.org_path(path))
            self.assertLess(r.status_code, 500, path)

    def test_admin_can_use_modules(self):
        self.assertEqual(self.admin_client.get(f'/api/v1/orgs/{self.org.pk}/tasin-owners').status_code, 200)
        self.assertEqual(self.admin_client.get(f'/api/v1/orgs/{self.org.pk}/dashboard/kpis').status_code, 200)

    def test_every_route_is_covered_by_a_rule(self):
        uncovered = []
        for method, path in routes(tasin.router):
            m = ORG_PATH.search(path)
            if m and required_permission(method, m.group(2)) is None:
                uncovered.append((method, path))
        self.assertEqual(uncovered, [])

    def test_portal_owner_role_is_not_a_super_admin(self):
        portal = User.objects.create_user('vehicle_owner', password='x')
        Membership.objects.create(organization=self.org, user=portal, role='owner')
        c = Client()
        c.force_login(portal)
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org.pk}/tasin-owners').status_code, 403)
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org.pk}/tasin-settings/users').status_code, 403)

    def test_super_admin_of_one_org_has_no_rights_in_another(self):
        sa = User.objects.create_user('sa_b', password='x')
        Membership.objects.create(organization=self.org_b, user=sa, role='super_admin')
        c = Client()
        c.force_login(sa)
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org_b.pk}/tasin-owners').status_code, 200)
        self.assertEqual(c.get(f'/api/v1/orgs/{self.org.pk}/tasin-owners').status_code, 403)

    def test_inactive_user_rejected(self):
        self.staff.is_active = False
        self.staff.save()
        self.assertEqual(self.viewer_client.get(f'/api/v1/orgs/{self.org.pk}/tasin-owners').status_code, 401)


class IdentitySpoofingTests(SecurityBase):
    def test_reviewer_cannot_be_chosen_in_payload_or_header(self):
        req = AccessRequest.objects.create(organization=self.org, user=self.staff, requested_module='owners', reason='x')
        url = f'/api/v1/orgs/{self.org.pk}/access-requests/{req.pk}/approve'
        r = self.viewer_client.post(url, data={'reviewer_username': 'admin', 'reviewer_id': self.admin.pk}, content_type='application/json', HTTP_X_USERNAME='admin')
        self.assertEqual(r.status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, 'Pending')
        r = self.viewer_client.post(url.replace('approve', 'reject'), data={'reviewer_username': 'admin', 'rejection_reason': 'no'}, content_type='application/json')
        self.assertEqual(r.status_code, 403)
        r = self.admin_client.post(url, data={}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_access_request_is_always_for_the_caller(self):
        r = self.viewer_client.post(f'/api/v1/orgs/{self.org.pk}/access-requests', data={'module': 'owners', 'reason': 'need it', 'user_id': self.admin.pk, 'username': 'admin'}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(AccessRequest.objects.get().user_id, self.staff.pk)

    def test_non_admin_cannot_read_other_users_permissions(self):
        self.assertEqual(self.viewer_client.get(f'/api/v1/orgs/{self.org.pk}/rbac/permissions?user_id={self.admin.pk}').status_code, 403)
        self.assertEqual(self.viewer_client.get(f'/api/v1/orgs/{self.org.pk}/tasin-settings/users/{self.admin.pk}/permissions').status_code, 403)

    def test_reserved_super_admin_usernames_cannot_be_registered(self):
        c = Client()
        for name in ('admin', 'SuperAdmin', 'sa_tasin_1'):
            r = c.post('/api/v1/auth/register', data={'organization_name': 'X', 'username': name, 'email': 'a@b.co', 'password': 'longenough1'}, content_type='application/json')
            self.assertEqual(r.status_code, 409, name)
