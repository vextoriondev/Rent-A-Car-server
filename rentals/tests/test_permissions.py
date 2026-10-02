from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client
from ..models import Organization, Membership, Party, PartyUser, Vehicle, Agreement, Branch
from .. import policies, services
from .base import RentalsTestCase

def login(user):
    c=Client();c.force_login(user);return c

class PermissionTests(RentalsTestCase):
    def setUp(self):
        super().setUp()
        User=get_user_model()
        self.users={}
        for role in ('admin','operations','viewer','mechanic'):
            self.users[role]=User.objects.create_user(role,password='x');Membership.objects.create(organization=self.org,user=self.users[role],role=role)
        self.users['accountant']=self.user
        for role,party in (('owner',self.owner),('driver',self.driver),('customer',self.customer)):
            u=User.objects.create_user(role,password='x');self.users[role]=u
            services.grant_party_access(self.org,party.pk,role)
        # a second owner with their own vehicle and agreement, which the first owner must never see
        self.other_owner=Party.objects.create(organization=self.org,kind='owner',name='Other Owner')
        self.other_car=Vehicle.objects.create(organization=self.org,registration='DHA-OTHER',model_name='Honda',owner=self.other_owner)
        self.other_agreement=Agreement.objects.create(organization=self.org,vehicle=self.other_car,customer=self.customer,driver=self.driver,start_date=date(2026,1,1),customer_rent=Decimal('1'),owner_rent=Decimal('1'),driver_salary=Decimal('1'))

    def get(self,role,path):return login(self.users[role]).get(f'/api/v1/orgs/{self.org.pk}{path}')

    def test_policy_matrix_matches_pre_m1_endpoint_roles(self):
        matrix={'period.close':{'admin','accountant'},'payment.post':{'admin','accountant'},'expense.record':{'admin','accountant','operations'},'vehicle.write':{'admin','operations'},'reopen':{'admin'}}
        every=set(policies.STAFF_ROLES|policies.ACTOR_ROLES)
        for action,allowed in matrix.items():
            self.assertEqual({r for r in every if policies.can(r,action)},allowed,action)
        for actor in policies.ACTOR_ROLES:
            for action in ('read_financial','draft','post','approve','reverse','settle','export','sensitive','party.write','payment.post'):self.assertFalse(policies.can(actor,action),(actor,action))
        self.assertFalse(policies.can('mechanic','post'));self.assertFalse(policies.can('viewer','draft'))

    def test_write_endpoints_enforce_roles(self):
        body={'kind':'owner','name':'N'}
        for role,status in (('admin',200),('operations',200),('viewer',403),('mechanic',403),('owner',403),('driver',403),('customer',403)):
            r=login(self.users[role]).post(f'/api/v1/orgs/{self.org.pk}/parties',body,content_type='application/json')
            self.assertEqual(r.status_code,status,role)
        self.assertEqual(login(self.users['operations']).post(f'/api/v1/orgs/{self.org.pk}/payments',{'kind':'customer_payment','amount':'1','reference':'r','event_date':'2026-09-01','allocations':[]},content_type='application/json').status_code,403)

    def test_other_organization_is_invisible(self):
        outsider=get_user_model().objects.create_user('outsider',password='x');other=Organization.objects.create(name='Other Co')
        Membership.objects.create(organization=other,user=outsider,role='admin')
        c=login(outsider)
        for path in ('/parties','/vehicles','/agreements','/periods','/expenses','/payments',f'/periods/{self.period.pk}/statement'):self.assertEqual(c.get(f'/api/v1/orgs/{self.org.pk}{path}').status_code,404,path)

    def test_owner_sees_only_own_vehicles_and_agreements(self):
        cars=self.get('owner','/vehicles').json();self.assertEqual([v['id'] for v in cars],[self.car.pk])
        items=self.get('owner','/agreements').json()['items'];self.assertEqual([a['id'] for a in items],[self.agreement.pk])
        self.assertEqual([p['id'] for p in self.get('owner','/parties').json()],[self.owner.pk])
        self.assertEqual({p['id'] for p in self.get('owner','/periods').json()},{self.period.pk})

    def test_driver_never_sees_customer_rent_or_owner_terms(self):
        item=next(a for a in self.get('driver','/agreements').json()['items'] if a['id']==self.agreement.pk)
        self.assertNotIn('customer_rent',item);self.assertNotIn('owner_rent',item);self.assertNotIn('owner',item);self.assertNotIn('customer',item)
        self.assertEqual(item['driver_salary'],'20000.00')
        text=str(item);self.assertNotIn('100000',text);self.assertNotIn('60000',text)
        self.assertEqual(self.get('driver','/vehicles').status_code,200)

    def test_customer_sees_own_rent_only(self):
        item=next(a for a in self.get('customer','/agreements').json()['items'] if a['id']==self.agreement.pk)
        self.assertEqual(item['customer_rent'],'100000.00');self.assertNotIn('owner_rent',item);self.assertNotIn('driver_salary',item);self.assertNotIn('driver',item)

    def test_actors_cannot_read_financial_endpoints(self):
        for role in ('owner','driver','customer','mechanic'):
            for path in ('/expenses','/payments',f'/periods/{self.period.pk}/statement'):self.assertEqual(self.get(role,path).status_code,403,(role,path))
            self.assertEqual(self.get(role,'/snapshot?month=2026-09-01').status_code,403)

    def test_mechanic_sees_vehicles_only(self):
        self.assertEqual(len(self.get('mechanic','/vehicles').json()),2)
        self.assertEqual(self.get('mechanic','/agreements').json()['items'],[])
        self.assertEqual(self.get('mechanic','/parties').json(),[])

    def test_sensitive_party_data_is_staff_finance_only(self):
        services.set_consent(self.org,self.owner.pk,sms=True,preferred_channel='sms',source='signed form')
        services.add_contact(self.org,self.customer.pk,{'name':'Accounts Desk','role':'billing','email':'ap@meridian.example'})
        admin_view={p['id']:p for p in self.get('admin','/parties').json()}
        self.assertTrue(admin_view[self.owner.pk]['consent']['sms']);self.assertEqual(admin_view[self.customer.pk]['contacts'][0]['role'],'billing')
        for role in ('operations','viewer','owner'):self.assertNotIn('consent',self.get(role,'/parties').json()[0],role)

    def test_me_reports_roles_branch_and_linked_parties(self):
        branch=services.default_branch(self.org);m=Membership.objects.get(user=self.users['owner']);m.branch=branch;m.save()
        org=login(self.users['owner']).get('/api/v1/me').json()['organizations'][0]
        self.assertEqual(org['role'],'owner');self.assertTrue(org['actor']);self.assertEqual(org['branch']['name'],'Main')
        self.assertEqual(org['parties'],[{'id':self.owner.pk,'kind':'owner','name':self.owner.name}])
        self.assertFalse(login(self.users['admin']).get('/api/v1/me').json()['organizations'][0]['actor'])

class IdentityServiceTests(RentalsTestCase):
    def test_consent_requires_consent_for_preferred_channel(self):
        with self.assertRaises(services.DomainError):services.set_consent(self.org,self.owner.pk,preferred_channel='whatsapp')
        p=services.set_consent(self.org,self.owner.pk,whatsapp=True,preferred_channel='whatsapp',source='call');self.assertIsNotNone(p.consent_updated_at);self.assertEqual(p.consent_source,'call')

    def test_grant_access_locks_role_to_party_kind_and_revokes(self):
        get_user_model().objects.create_user('own1',password='x')
        link=services.grant_party_access(self.org,self.owner.pk,'own1');self.assertEqual(Membership.objects.get(user__username='own1').role,'owner')
        services.revoke_party_access(self.org,self.owner.pk,link.user_id);self.assertFalse(PartyUser.objects.get(pk=link.pk).active)
        with self.assertRaises(services.DomainError):services.grant_party_access(self.org,self.driver.pk,'accountant')   # staff login cannot be turned into an actor
        with self.assertRaises(services.DomainError):services.grant_party_access(self.org,self.owner.pk,'nobody')

    def test_cannot_touch_party_of_another_organization(self):
        other=Organization.objects.create(name='Other');stranger=Party.objects.create(organization=other,kind='owner',name='S')
        with self.assertRaises(Party.DoesNotExist):services.update_party(self.org,stranger.pk,{'name':'x'})
        with self.assertRaises(Party.DoesNotExist):services.add_contact(self.org,stranger.pk,{'name':'x'})

    def test_branch_unique_and_default_idempotent(self):
        self.assertEqual(services.default_branch(self.org).pk,services.default_branch(self.org).pk)
        services.create_branch(self.org,'Chattogram')
        from django.db import IntegrityError,transaction
        with self.assertRaises(IntegrityError),transaction.atomic():services.create_branch(self.org,'Chattogram')
        self.assertEqual(Branch.objects.filter(organization=self.org).count(),2)
