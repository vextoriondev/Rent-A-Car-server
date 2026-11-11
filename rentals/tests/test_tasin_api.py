from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from rentals.models import Organization, Branch, Membership

class TasinApiTests(TestCase):
    def setUp(self):
        self.c = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='admin_test', password='password123')
        self.org = Organization.objects.create(name='TASIN ENTERPRISE')
        self.branch = Branch.objects.create(organization=self.org, name='Main')
        Membership.objects.create(organization=self.org, user=self.user, role='admin', branch=self.branch)
        # Login client
        self.c.force_login(self.user)

    def test_health_and_connection(self):
        r1 = self.c.get('/api/v1/health')
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()['status'], 'ok')

        r2 = self.c.get('/api/v1/connection/status')
        self.assertEqual(r2.status_code, 200)
        data = r2.json()
        self.assertTrue(data['connected'])
        self.assertEqual(data['total_modules'], 15)

    def test_dashboard_kpis(self):
        r = self.c.get(f'/api/v1/orgs/{self.org.pk}/dashboard/kpis')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn('vehicles', data)
        self.assertIn('financials', data)
        self.assertIn('today', data)

    def test_owner_lifecycle(self):
        # Create Owner
        r_create = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-owners',
            data={
                'name': 'Test Owner', 'phone': '01700000001', 'alt_mobile': '01900000001',
                'email': 'owner@test.com', 'nid': '1234567890', 'present_address': 'Dhaka',
                'permanent_address': 'Chittagong', 'bank_name': 'DBBL', 'account_name': 'Test Owner',
                'account_number': '123456', 'branch_name': 'Dhanmondi', 'account_type': 'Savings',
                'joining_date': '2026-01-01', 'status': 'Active'
            },
            content_type='application/json'
        )
        self.assertEqual(r_create.status_code, 200)
        owner_id = r_create.json()['id']

        # List owners
        r_list = self.c.get(f'/api/v1/orgs/{self.org.pk}/tasin-owners')
        self.assertEqual(r_list.status_code, 200)
        self.assertTrue(any(o['name'] == 'Test Owner' for o in r_list.json()))

        # Pay owner
        r_pay = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-owners/{owner_id}/payments',
            data={'amount': 25000, 'payment_method': 'Bank Transfer', 'reference': 'REF-101', 'note': 'Monthly rent payout'},
            content_type='application/json'
        )
        self.assertEqual(r_pay.status_code, 200)
        self.assertTrue(r_pay.json()['success'])

    def test_vehicle_lifecycle(self):
        r_create = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-vehicles',
            data={
                'registration': 'DHA-GA-11-2233', 'brand': 'Toyota', 'model': 'Premio',
                'year': 2022, 'category': 'Sedan', 'color': 'White', 'monthly_rent': 55000,
                'status': 'Available'
            },
            content_type='application/json'
        )
        self.assertEqual(r_create.status_code, 200)
        veh_id = r_create.json()['id']

        # Add paper
        r_paper = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-vehicles/{veh_id}/papers',
            data={'paper_type': 'fitness', 'paper_number': 'FIT-9900', 'expiry_date': '2027-10-10', 'status': 'Valid'},
            content_type='application/json'
        )
        self.assertEqual(r_paper.status_code, 200)

        # Get papers
        r_get_papers = self.c.get(f'/api/v1/orgs/{self.org.pk}/tasin-vehicles/{veh_id}/papers')
        self.assertEqual(r_get_papers.status_code, 200)
        self.assertEqual(len(r_get_papers.json()), 1)

    def test_duty_lifecycle(self):
        r_create = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-duties',
            data={
                'date': '2026-11-10', 'client_name': 'Test Client', 'vehicle_reg': 'DHA-GA-99-8877',
                'driver_name': 'Test Driver', 'pickup_location': 'Uttara', 'destination': 'Gulshan',
                'start_time': '09:00', 'end_time': '18:00', 'duty_type': 'Daily', 'agreed_rent': 6000,
                'fuel_cost': 1500, 'driver_allowance': 300, 'status': 'Scheduled'
            },
            content_type='application/json'
        )
        self.assertEqual(r_create.status_code, 200)
        duty_id = r_create.json()['id']
        self.assertEqual(r_create.json()['totalAmount'], 7800)

        # Change status
        r_st = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-duties/{duty_id}/status',
            data={'status': 'Running'},
            content_type='application/json'
        )
        self.assertEqual(r_st.status_code, 200)
        self.assertEqual(r_st.json()['status'], 'Running')

    def test_garage_and_maintenance(self):
        r_grg = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-garages',
            data={'name': 'Central Workshop', 'contact_person': 'Karim', 'mobile': '01800000002', 'address': 'Tejgaon'},
            content_type='application/json'
        )
        self.assertEqual(r_grg.status_code, 200)

        r_mnt = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-maintenance',
            data={
                'vehicle_reg': 'DHA-GA-11-2233', 'garage_name': 'Central Workshop',
                'date': '2026-11-10', 'problem_description': 'Oil leak', 'service_type': 'Engine',
                'labour_cost': 2000, 'parts_cost': 3500, 'status': 'In Progress'
            },
            content_type='application/json'
        )
        self.assertEqual(r_mnt.status_code, 200)
        self.assertEqual(r_mnt.json()['totalCost'], 5500)

    def test_accounts_and_transfer(self):
        r1 = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-accounts',
            data={'name': 'Cash Box', 'account_type': 'Cash', 'opening_balance': 50000},
            content_type='application/json'
        )
        r2 = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-accounts',
            data={'name': 'City Bank', 'account_type': 'Bank', 'opening_balance': 200000},
            content_type='application/json'
        )
        id1 = r1.json()['id']
        id2 = r2.json()['id']

        # Transfer
        r_tx = self.c.post(
            f'/api/v1/orgs/{self.org.pk}/tasin-accounts/transfer',
            data={'from_account_id': int(id1), 'to_account_id': int(id2), 'amount': 10000, 'description': 'Cash deposit'},
            content_type='application/json'
        )
        self.assertEqual(r_tx.status_code, 200)
        self.assertTrue(r_tx.json()['success'])

    def test_due_collection_and_vehicle_search(self):
        r_due = self.c.get(f'/api/v1/orgs/{self.org.pk}/tasin-due-collection')
        self.assertEqual(r_due.status_code, 200)
        self.assertIn('totals', r_due.json())

        r_srch = self.c.get(f'/api/v1/orgs/{self.org.pk}/tasin-vehicle-search?q=')
        self.assertEqual(r_srch.status_code, 200)

