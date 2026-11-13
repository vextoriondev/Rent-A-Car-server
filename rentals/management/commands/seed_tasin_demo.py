import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rentals.models import (
    Organization, Branch, Membership, Party, Vehicle,
    VehicleDetail, PartyDetail, Duty, Garage, MaintenanceRecord,
    VehiclePaper, FinancialAccount, AccountTransaction, SystemAlert, CompanyProfile,
    TasinInvoice, TasinInvoiceItem, TasinInvoicePayment,
    DriverAttendance, SparePart, PartConsumption, DueFollowUp,
    CustomReminder, TripDispatch, PrivateDocument, Agreement, AgreementVersion,
    AuditEvent
)

D = Decimal

class Command(BaseCommand):
    help = 'Seeds realistic synthetic demo data for TASIN ENTERPRISE across all modules safely.'

    def add_arguments(self, parser):
        parser.add_argument('--allow-production-demo-org', action='store_true',
                            help='Explicitly allow seeding demo data into an isolated DEMO organization in production.')
        parser.add_argument('--org-name', default='TASIN ENTERPRISE (TEST)',
                            help='Organization name for demo records.')

    def handle(self, *args, **options):
        db_host = settings.DATABASES['default'].get('HOST', '')
        is_production = 'neon.tech' in db_host or 'railway' in db_host

        # SAFETY GUARD: Fail-closed if production database is detected without explicit flag
        if is_production and not options['allow_production_demo_org']:
            raise CommandError(
                "SAFETY GUARD BLOCKED: Active database appears to be production Neon/Railway ("
                f"{db_host}). Refusing to insert synthetic demo data. "
                "To test safely, use local test database (e.g., $env:USE_SQLITE='1') or provide "
                "--allow-production-demo-org with an isolated test org name."
            )

        org_name = options['org_name']
        self.stdout.write(self.style.NOTICE(f"Seeding synthetic demo data into organization: '{org_name}'..."))

        with transaction.atomic():
            # 1. Organization & System RBAC Users (Super Admin, Admin, Accountant, Operations Manager, Staff)
            User = get_user_model()
            users_meta = [
                ('superadmin', 'super@tasin.com', 'SuperAdmin2026!', 'super_admin', True, True),
                ('tasin_admin', 'admin@tasin.com', 'TasinAdmin2026!', 'admin', True, False),
                ('accountant_arif', 'arif@tasin.com', 'ArifAcct2026!', 'accountant', True, False),
                ('ops_tanvir', 'tanvir@tasin.com', 'TanvirOps2026!', 'operations_manager', True, False),
                ('staff_rakib', 'rakib@tasin.com', 'RakibStaff2026!', 'staff', False, False),
            ]
            
            created_users = {}
            for u_name, u_email, u_pass, u_role, is_staff, is_super in users_meta:
                u, _ = User.objects.get_or_create(
                    username=u_name,
                    defaults={'email': u_email, 'is_staff': is_staff, 'is_superuser': is_super}
                )
                u.set_password(u_pass)
                u.save()
                created_users[u_role] = u

            org, _ = Organization.objects.get_or_create(name=org_name)
            # Secondary isolated organization for cross-org testing
            org2, _ = Organization.objects.get_or_create(name='TASIN ELITE RENTALS (TEST)')
            
            branch, _ = Branch.objects.get_or_create(organization=org, name='Dhaka Central')
            for u_role, u in created_users.items():
                Membership.objects.update_or_create(
                    organization=org, user=u,
                    defaults={'role': u_role, 'branch': branch}
                )

            CompanyProfile.objects.update_or_create(
                organization=org,
                defaults={
                    'company_name': org_name,
                    'address': 'Level 8, Motijheel C/A, Dhaka-1000, Bangladesh',
                    'phone': '+880 1711-234567',
                    'email': 'info@tasinenterprise.com',
                    'website': 'https://tasinenterprise.com',
                    'bin': 'BIN-002918274-0101',
                    'tin': 'TIN-992817263541',
                    'details': 'TASIN ENTERPRISE – Premium Rent a Car Management Software',
                    'currency': 'BDT',
                    'timezone': 'Asia/Dhaka',
                    'date_format': 'YYYY-MM-DD'
                }
            )

            # 2. Financial Accounts (5 accounts)
            acc_data = [
                ('ACC-001', 'Main Cash Account', 'Cash', '', 'Cash In Hand', 'Head Office', D('500000')),
                ('ACC-002', 'DBBL Current Account', 'Bank', '110.120.33445', 'Dutch-Bangla Bank', 'Motijheel Branch', D('1850000')),
                ('ACC-003', 'BRAC Bank Corporate', 'Bank', '150.230.99881', 'BRAC Bank', 'Gulshan Branch', D('1250000')),
                ('ACC-004', 'City Bank Islamic', 'Bank', '210.340.55667', 'City Bank', 'Banani Branch', D('650000')),
                ('ACC-005', 'bKash Merchant Account', 'Mobile Banking', '01711-000999', 'bKash', 'Corporate Division', D('185000')),
            ]
            accounts = {}
            for acc_id, name, atype, num, bname, brname, op_bal in acc_data:
                a, _ = FinancialAccount.objects.update_or_create(
                    organization=org, account_id=acc_id,
                    defaults={
                        'name': name, 'account_type': atype, 'account_number': num,
                        'bank_name': bname, 'branch_name': brname,
                        'opening_balance': op_bal, 'current_balance': op_bal,
                        'is_active': True
                    }
                )
                accounts[acc_id] = a

            # 3. Garages (5 garages)
            garage_data = [
                ('GRG-001', 'Rahman Auto Works', 'Mokhlesur Rahman', '01712-111222', 'Tejgaon I/A, Dhaka', 'Engine', D('145000'), D('85000')),
                ('GRG-002', 'Bengal Motor Care', 'Belal Hossain', '01812-333444', 'Mirpur 10, Dhaka', 'General', D('95000'), D('55000')),
                ('GRG-003', 'Apex Body & Paint', 'Shafiqul Alam', '01912-555666', 'Kakrail, Dhaka', 'Body Works', D('75000'), D('45000')),
                ('GRG-004', 'CoolAir AC Solution', 'Nasir Uddin', '01612-777888', 'Mohakhali, Dhaka', 'AC Specialist', D('45000'), D('25000')),
                ('GRG-005', 'Trust Auto Electrical', 'Shahinur Islam', '01512-999000', 'Rampura, Dhaka', 'Electrical', D('35000'), D('20000')),
            ]
            garages = {}
            for gid, name, contact, mobile, addr, gtype, bill, paid in garage_data:
                g, _ = Garage.objects.update_or_create(
                    organization=org, garage_id=gid,
                    defaults={
                        'name': name, 'contact_person': contact, 'mobile': mobile,
                        'address': addr, 'garage_type': gtype, 'status': 'Active',
                        'total_bill': bill, 'total_paid': paid
                    }
                )
                garages[name] = g

            # 4. Owners (8 owners)
            owner_data = [
                ('Farhana Rahman', '01711-234567', 'farhana@tasin.com', 'Dhanmondi, Dhaka', 'Dutch-Bangla Bank', '2241001234', D('250000'), D('180000')),
                ('Rashed Karim', '01811-345678', 'rashed@tasin.com', 'Green Road, Dhaka', 'BRAC Bank', '3351002345', D('180000'), D('120000')),
                ('Nadia Sultana', '01611-456789', 'nadia@tasin.com', 'Mirpur-10, Dhaka', 'Islami Bank', '2051003456', D('210000'), D('150000')),
                ('Mahmudul Hasan', '01511-567890', 'mahmud@tasin.com', 'Gulshan-2, Dhaka', 'Sonali Bank', '0193004567', D('320000'), D('240000')),
                ('Tariqul Islam', '01911-678901', 'tariqul@tasin.com', 'Uttara Sector 4, Dhaka', 'Eastern Bank', '1041005678', D('160000'), D('110000')),
                ('Salma Begum', '01711-789012', 'salma@tasin.com', 'Banani, Dhaka', 'City Bank', '4401006789', D('140000'), D('95000')),
                ('Kazi Jahangir', '01811-890123', 'kazi@tasin.com', 'Mohammadpur, Dhaka', 'Trust Bank', '5501007890', D('190000'), D('130000')),
                ('Mustafizur Rahman', '01911-901234', 'mustafiz@tasin.com', 'Baridhara, Dhaka', 'Mutual Trust Bank', '6601008901', D('220000'), D('160000')),
            ]
            owners = []
            for name, mobile, email, addr, bank, acc_no, pay, paid in owner_data:
                p, _ = Party.objects.get_or_create(organization=org, name=name, kind=Party.Kind.OWNER, defaults={'phone': mobile, 'email': email})
                PartyDetail.objects.update_or_create(
                    party=p,
                    defaults={
                        'present_address': addr, 'bank_name': bank, 'account_number': acc_no,
                        'total_payable_owner': pay, 'total_paid_owner': paid, 'status': 'Active'
                    }
                )
                owners.append(p)

            # 5. Drivers (15 drivers)
            driver_data = [
                ('Kamal Hossain', '01722-111111', 'DL-DH-10291', date.today() + timedelta(days=180), D('22000'), D('3000'), D('18000')),
                ('Hasan Ali', '01822-222222', 'DL-DH-20392', date.today() - timedelta(days=10), D('20000'), D('0'), D('15000')), # Expired
                ('Sabbir Ahmed', '01922-333333', 'DL-DH-30493', date.today() + timedelta(days=5), D('24000'), D('2000'), D('20000')),  # Expiring 5d
                ('Jamal Uddin', '01622-444444', 'DL-DH-40594', date.today() + timedelta(days=14), D('21000'), D('0'), D('17000')), # Expiring 14d
                ('Rafiqul Islam', '01522-555555', 'DL-DH-50695', date.today() + timedelta(days=28), D('23000'), D('1500'), D('19000')), # Expiring 28d
                ('Anwar Hossain', '01733-666666', 'DL-DH-60796', date.today() + timedelta(days=300), D('20000'), D('0'), D('16000')),
                ('Mizanur Rahman', '01833-777777', 'DL-DH-70897', date.today() + timedelta(days=240), D('25000'), D('4000'), D('20000')),
                ('Shafiqul Islam', '01933-888888', 'DL-DH-80998', date.today() + timedelta(days=90), D('21000'), D('0'), D('18000')),
                ('Alamgir Kabir', '01744-112233', 'DL-DH-91099', date.today() + timedelta(days=360), D('22000'), D('1000'), D('19000')),
                ('Zahirul Haque', '01844-223344', 'DL-DH-12100', date.today() + timedelta(days=150), D('23000'), D('0'), D('20000')),
                ('Babul Mia', '01944-334455', 'DL-DH-23201', date.today() + timedelta(days=200), D('19000'), D('2000'), D('16000')),
                ('Habibur Rahman', '01644-445566', 'DL-DH-34302', date.today() + timedelta(days=80), D('24000'), D('0'), D('21000')),
                ('Shahidul Islam', '01544-556677', 'DL-DH-45403', date.today() + timedelta(days=220), D('20000'), D('1500'), D('17000')),
                ('Monir Hossain', '01755-667788', 'DL-DH-56504', date.today() + timedelta(days=320), D('22000'), D('0'), D('19000')),
                ('Nazmul Huda', '01855-778899', 'DL-DH-67605', date.today() + timedelta(days=400), D('25000'), D('3000'), D('21000')),
            ]
            drivers = []
            for name, mobile, lic, exp, sal, adv, paid in driver_data:
                d, _ = Party.objects.get_or_create(organization=org, name=name, kind=Party.Kind.DRIVER, defaults={'phone': mobile})
                PartyDetail.objects.update_or_create(
                    party=d,
                    defaults={
                        'license_no': lic, 'license_expiry': exp,
                        'monthly_salary': sal, 'advance': adv, 'total_paid_driver': paid,
                        'status': 'Active'
                    }
                )
                drivers.append(d)

            # 6. Vehicles (20 vehicles)
            vehicle_specs = [
                ('DHA-METRO-GA-19-7724', 'Toyota', 'Premio', 2021, 'Sedan', 'Available', D('55000'), owners[0], drivers[0]),
                ('DHA-METRO-GA-22-4811', 'Toyota', 'Allion', 2020, 'Sedan', 'On Duty', D('50000'), owners[1], drivers[1]),
                ('DHA-METRO-CHA-15-3390', 'Toyota', 'Noah', 2019, 'Microbus', 'On Duty', D('65000'), owners[2], drivers[2]),
                ('DHA-METRO-CHA-53-8812', 'Toyota', 'HiAce', 2022, 'Microbus', 'Available', D('85000'), owners[3], drivers[3]),
                ('DHA-METRO-GHA-11-2099', 'Toyota', 'Prado', 2021, 'SUV', 'Available', D('130000'), owners[4], drivers[4]),
                ('DHA-METRO-GA-35-9012', 'Honda', 'Grace', 2020, 'Sedan', 'Available', D('48000'), owners[5], drivers[5]),
                ('DHA-METRO-GA-41-1123', 'Toyota', 'Axio', 2022, 'Sedan', 'On Duty', D('45000'), owners[6], drivers[6]),
                ('DHA-METRO-CHA-28-4455', 'Toyota', 'Voxy', 2021, 'Microbus', 'In Garage', D('70000'), owners[7], drivers[7]),
                ('DHA-METRO-GA-14-5566', 'Nissan', 'X-Trail', 2020, 'SUV', 'Available', D('95000'), owners[0], drivers[8]),
                ('DHA-METRO-CHA-39-6677', 'Toyota', 'Esquire', 2022, 'Microbus', 'On Duty', D('80000'), owners[1], drivers[9]),
                ('DHA-METRO-GA-27-7788', 'Mitsubishi', 'Pajero', 2019, 'SUV', 'Available', D('120000'), owners[2], drivers[10]),
                ('DHA-METRO-GA-33-8899', 'Toyota', 'Corolla Cross', 2023, 'SUV', 'Available', D('90000'), owners[3], drivers[11]),
                ('DHA-METRO-CHA-45-9900', 'Toyota', 'Super GL', 2020, 'Microbus', 'Available', D('75000'), owners[4], drivers[12]),
                ('DHA-METRO-GA-18-1234', 'Hyundai', 'Sonata', 2021, 'Sedan', 'On Duty', D('60000'), owners[5], drivers[13]),
                ('DHA-METRO-GA-25-2345', 'Toyota', 'Camry', 2022, 'Sedan', 'Available', D('85000'), owners[6], drivers[14]),
                ('DHA-METRO-GHA-14-3456', 'Toyota', 'Harrier', 2021, 'SUV', 'Available', D('110000'), owners[7], drivers[0]),
                ('DHA-METRO-GA-39-4567', 'Honda', 'Civic', 2020, 'Sedan', 'Available', D('52000'), owners[0], drivers[1]),
                ('DHA-METRO-CHA-51-5678', 'Nissan', 'Urvan', 2019, 'Microbus', 'Available', D('68000'), owners[1], drivers[2]),
                ('DHA-METRO-GA-44-6789', 'Toyota', 'Yaris Cross', 2023, 'SUV', 'On Duty', D('75000'), owners[2], drivers[3]),
                ('DHA-METRO-GA-50-7890', 'Kia', 'Sportage', 2022, 'SUV', 'Available', D('88000'), owners[3], drivers[4]),
            ]
            vehicles = []
            for reg, brand, model, yr, cat, st, rent, owner, driver in vehicle_specs:
                v, _ = Vehicle.objects.get_or_create(
                    organization=org, registration=reg,
                    defaults={'model_name': f"{brand} {model}", 'owner': owner}
                )
                VehicleDetail.objects.update_or_create(
                    vehicle=v,
                    defaults={
                        'brand': brand, 'model': model, 'year': yr,
                        'category': cat, 'status': st, 'monthly_rent': rent,
                        'chassis_no': f'CHS-{reg[-4:]}-8829', 'engine_no': f'ENG-{reg[-4:]}-1102',
                        'assigned_driver': driver, 'mobil_count': 3, 'last_mobil_date': date.today() - timedelta(days=20)
                    }
                )
                vehicles.append(v)

            # 7. Clients / Companies (15 clients)
            client_data = [
                ('Square Pharmaceuticals Ltd', 'Square Group', 'Tanvir Ahmed', '01713-100100', 'tanvir@square.com', D('350000'), D('240000')),
                ('Beximco Communications Ltd', 'Beximco', 'Sharmin Akter', '01813-200200', 'sharmin@beximco.net', D('280000'), D('190000')),
                ('Akij Venture Resources', 'Akij Group', 'Kamrul Hasan', '01913-300300', 'kamrul@akij.net', D('195000'), D('140000')),
                ('Apex Footwear Logistics', 'Apex Group', 'Sayeed Hossain', '01613-400400', 'sayeed@apex.com.bd', D('420000'), D('310000')),
                ('PRAN-RFL Commercial Ops', 'PRAN Group', 'Asaduzzaman', '01513-500500', 'asad@pran.com.bd', D('310000'), D('210000')),
                ('Grameenphone Network Fleet', 'Telenor GP', 'Mahbub Alam', '01713-600600', 'mahbub@grameenphone.com', D('480000'), D('380000')),
                ('Robi Axiata Operations', 'Axiata Group', 'Nuzhat Jahan', '01813-700700', 'nuzhat@robi.com.bd', D('260000'), D('180000')),
                ('BRAC Social Enterprise', 'BRAC', 'Tariq Anam', '01913-800800', 'tariq@brac.net', D('220000'), D('160000')),
                ('Walton Hi-Tech Fleet', 'Walton', 'Abdur Rahim', '01613-900900', 'rahim@waltonbd.com', D('340000'), D('250000')),
                ('Bashundhara Paper Ops', 'Bashundhara', 'Mustafizur Rahman', '01513-010101', 'mustafiz@bgd.com', D('390000'), D('270000')),
                ('ACI Logistics (Shwapno)', 'ACI Group', 'Shafiqul Islam', '01713-121212', 'shafiq@aci-bd.com', D('210000'), D('150000')),
                ('Eskayef Pharmaceuticals', 'Transcom', 'Zakir Hossain', '01813-232323', 'zakir@skf.com', D('300000'), D('220000')),
                ('Meghna Group Fleet', 'Meghna', 'Rezaul Karim', '01913-343434', 'rezaul@mgi.org', D('250000'), D('180000')),
                ('Standard Chartered VIP', 'SCB Bank', 'Farzana Yasmin', '01613-454545', 'farzana@sc.com', D('180000'), D('130000')),
                ('Unilever BD Field Team', 'Unilever', 'Tahmidur Rahman', '01513-565656', 'tahmid@unilever.com', D('290000'), D('200000')),
            ]
            clients = []
            for name, grp, contact, phone, email, rec, col in client_data:
                c, _ = Party.objects.get_or_create(
                    organization=org, name=name, kind=Party.Kind.CUSTOMER,
                    defaults={'phone': phone, 'email': email}
                )
                PartyDetail.objects.update_or_create(
                    party=c,
                    defaults={
                        'contact_person': contact, 'trade_license': f'TRD-DHK-{phone[-4:]}',
                        'total_receivable_client': rec, 'total_collected_client': col,
                        'status': 'Active'
                    }
                )
                clients.append(c)

            # 8. Duties (60 duties: scheduled, running, completed)
            today = date.today()
            duties = []
            for i in range(1, 61):
                d_id = f'DTY-{i:03d}'
                d_date = today - timedelta(days=(i % 30))
                cl = clients[i % len(clients)]
                vh = vehicles[i % len(vehicles)]
                dr = drivers[i % len(drivers)]
                rent = D(str(3500 + (i * 120) % 6000))
                fuel = D(str(1000 + (i * 50) % 2500))
                allow = D(str(300 + (i * 20) % 500))
                status = 'Completed' if i > 6 else ('Running' if i > 3 else 'Scheduled')
                total = rent + fuel + allow

                d, _ = Duty.objects.update_or_create(
                    organization=org, duty_id=d_id,
                    defaults={
                        'date': d_date, 'client': cl, 'client_name': cl.name,
                        'vehicle': vh, 'vehicle_reg': vh.registration,
                        'driver': dr, 'driver_name': dr.name,
                        'pickup_location': 'Gulshan-1, Dhaka',
                        'destination': 'Savar EPZ, Dhaka' if i % 2 == 0 else 'Chittagong Port',
                        'start_time': '08:00 AM', 'end_time': '06:00 PM',
                        'agreed_rent': rent, 'fuel_cost': fuel, 'driver_allowance': allow,
                        'total_amount': total, 'status': status
                    }
                )
                duties.append(d)

            # 9. Invoices (30 invoices with line items & partial/full payments)
            invoices = []
            for i in range(1, 31):
                inv_no = f'INV-2026-{i:04d}'
                cl = clients[i % len(clients)]
                sub = D(str(15000 + (i * 850) % 40000))
                paid = sub if i % 3 == 0 else (sub / 2 if i % 3 == 1 else D(0))
                st = 'Paid' if paid == sub else ('Partially Paid' if paid > 0 else 'Sent')

                inv, _ = TasinInvoice.objects.update_or_create(
                    organization=org, invoice_number=inv_no,
                    defaults={
                        'client': cl, 'client_name': cl.name,
                        'issue_date': today - timedelta(days=i),
                        'due_date': today + timedelta(days=15 - i),
                        'subtotal': sub, 'total_amount': sub, 'paid_amount': paid,
                        'status': st, 'notes': f'Monthly fleet service for {cl.name}'
                    }
                )
                invoices.append(inv)
                # Invoice line item
                TasinInvoiceItem.objects.update_or_create(
                    invoice=inv, description=f'Monthly vehicle rental & duty charges',
                    defaults={'quantity': D(1), 'unit_price': sub, 'total_price': sub}
                )
                # Payment if paid > 0
                if paid > 0:
                    TasinInvoicePayment.objects.update_or_create(
                        invoice=inv, payment_id=f'PAY-INV-{i:03d}',
                        defaults={
                            'date': today - timedelta(days=i // 2),
                            'amount': paid,
                            'payment_method': 'Bank Transfer' if i % 2 == 0 else 'Cash',
                            'account': accounts['ACC-002'] if i % 2 == 0 else accounts['ACC-001'],
                            'reference': f'TXN-INV-REF-{i:03d}'
                        }
                    )

            # 10. Expenses (50 operational expenses)
            exp_cats = ['Fuel', 'Parking', 'Toll', 'Driver Food / Allowance', 'Office Rent', 'Utilities', 'Vehicle Repair']
            for i in range(1, 51):
                AccountTransaction.objects.update_or_create(
                    organization=org, transaction_id=f'EXP-{i:03d}',
                    defaults={
                        'date': today - timedelta(days=(i % 25)),
                        'transaction_type': 'Expense',
                        'category': exp_cats[i % len(exp_cats)],
                        'related_person': f'Vendor {i}',
                        'vehicle_reg': vehicles[i % len(vehicles)].registration,
                        'amount': D(str(500 + (i * 150) % 5000)),
                        'payment_method': 'Cash' if i % 2 == 0 else 'Bank',
                        'description': f'Operational cost item #{i}',
                        'reference': f'EXP-REF-{i:03d}'
                    }
                )

            # 11. Maintenance Records (30 maintenance cases)
            m_services = ['Oil Change', 'General Service', 'Brake Pad Replacement', 'AC Servicing', 'Suspension Overhaul']
            g_names = list(garages.keys())
            for i in range(1, 31):
                stype = m_services[i % len(m_services)]
                labour = D(str(1500 + (i * 100) % 4000))
                parts = D(str(2500 + (i * 200) % 8000))
                MaintenanceRecord.objects.update_or_create(
                    organization=org, maintenance_id=f'MNT-{i:03d}',
                    defaults={
                        'vehicle_reg': vehicles[i % len(vehicles)].registration,
                        'garage_name': g_names[i % len(g_names)],
                        'date': today - timedelta(days=i * 2),
                        'problem_description': f'Scheduled service: {stype}',
                        'service_type': stype,
                        'parts_details': 'Filters, Mobil, Bushing, Brake fluid',
                        'labour_cost': labour,
                        'parts_cost': parts,
                        'total_cost': labour + parts,
                        'status': 'Completed' if i > 5 else 'In Progress'
                    }
                )

            # 12. Vehicle Papers (40 papers: fitness, tax token, insurance, route permit)
            p_types = ['fitness', 'tax_token', 'insurance', 'route_permit']
            for idx, v in enumerate(vehicles):
                for p_type in p_types:
                    exp_delta = 180 if idx % 4 == 0 else (12 if idx % 4 == 1 else (-15 if idx % 4 == 2 else 5))
                    VehiclePaper.objects.update_or_create(
                        organization=org, vehicle=v, paper_type=p_type,
                        defaults={
                            'paper_number': f'PPR-{v.registration[-4:]}-{p_type[:3].upper()}',
                            'issue_date': today - timedelta(days=200),
                            'expiry_date': today + timedelta(days=exp_delta)
                        }
                    )

            # 13. Driver Attendance (60+ records across drivers)
            for d in drivers[:8]:
                for day_offset in range(10):
                    DriverAttendance.objects.update_or_create(
                        organization=org, driver=d, date=today - timedelta(days=day_offset),
                        defaults={'status': 'Present' if day_offset % 5 != 0 else 'On Duty', 'notes': 'Routine shift'}
                    )

            # 14. Due Follow-ups (20 records)
            for i in range(1, 21):
                cl = clients[i % len(clients)]
                DueFollowUp.objects.update_or_create(
                    organization=org, party=cl, follow_up_date=today - timedelta(days=i),
                    defaults={
                        'party_type': 'Customer',
                        'contact_method': 'Phone Call' if i % 2 == 0 else 'WhatsApp',
                        'notes': f'Discussed outstanding receivable of BDT 15,000 for invoice #{i}',
                        'next_follow_up_date': today + timedelta(days=7),
                        'status': 'Pending' if i <= 10 else 'Completed',
                        'responsible_staff': 'Arif (Accountant)'
                    }
                )

            # 15. Custom Reminders (15 records)
            for i in range(1, 16):
                CustomReminder.objects.update_or_create(
                    organization=org, title=f'Payment follow-up with {clients[i % len(clients)].name}',
                    defaults={
                        'reminder_date': today + timedelta(days=(i % 10)),
                        'reminder_time': '10:00 AM',
                        'description': f'Verify bank deposit slip for corporate trip #{i}',
                        'related_entity_type': 'Client',
                        'related_entity_id': str(clients[i % len(clients)].id),
                        'assigned_staff': 'Tanvir (Operations)',
                        'status': 'Pending' if i <= 10 else 'Completed'
                    }
                )

            # 16. Spare Parts Inventory (10 items)
            part_specs = [
                ('PRT-MOB-01', 'Total Quartz 9000 Engine Oil (4L)', 'Mobil/Oil', 'Can', 25, 3400, 5),
                ('PRT-MOB-02', 'Mobil 1 Fully Synthetic 5W-30', 'Mobil/Oil', 'Litre', 40, 950, 8),
                ('PRT-FLT-01', 'Toyota Genuine Oil Filter (Premio/Allion)', 'Filter', 'Piece', 18, 650, 4),
                ('PRT-FLT-02', 'Air Filter Noah/HiAce', 'Filter', 'Piece', 12, 850, 3),
                ('PRT-BRK-01', 'Akebono Front Brake Pads', 'Brake', 'Set', 10, 2800, 2),
                ('PRT-BRK-02', 'Rear Brake Shoe Set (HiAce)', 'Brake', 'Set', 8, 2200, 2),
                ('PRT-BAT-01', 'Hamko Heavy Duty 75AH Battery', 'Battery', 'Piece', 6, 8500, 2),
                ('PRT-TYR-01', 'Bridgestone 195/65R15 Tyre', 'Tyre', 'Piece', 16, 7500, 4),
                ('PRT-CLT-01', 'Prestone Radiator Coolant (Green)', 'Mobil/Oil', 'Can', 15, 1200, 3),
                ('PRT-PLG-01', 'NGK Iridium Spark Plugs Set', 'Electrical', 'Set', 14, 3200, 4),
            ]
            for code, name, cat, unit, qty, cost, thresh in part_specs:
                SparePart.objects.update_or_create(
                    organization=org, part_code=code,
                    defaults={
                        'name': name, 'category': cat, 'unit': unit,
                        'quantity': D(str(qty)), 'cost_per_unit': D(str(cost)),
                        'supplier': 'Navana Petroleum & Spares', 'low_stock_threshold': D(str(thresh))
                    }
                )

            # 17. Audit Events (30 audit records)
            for i in range(1, 31):
                AuditEvent.objects.create(
                    organization=org,
                    actor=created_users['super_admin'],
                    action=f'AUDIT_TRANSACTION_LOG_{i}',
                    target_type='Duty' if i % 2 == 0 else 'Invoice',
                    target_id=str(i),
                    before={'status': 'Scheduled'},
                    after={'status': 'Completed'},
                    reason=f'System automated audit check #{i}',
                    ip='127.0.0.1'
                )

        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded comprehensive demo dataset into '{org_name}':\n"
            f"  - Organizations: 2 (Primary + Cross-Org Isolation)\n"
            f"  - RBAC Users: 5 (Super Admin, Admin, Accountant, Operations Manager, Staff)\n"
            f"  - Financial Accounts: {len(accounts)}\n"
            f"  - Garages: {len(garages)}\n"
            f"  - Owners: {len(owners)}\n"
            f"  - Drivers: {len(drivers)}\n"
            f"  - Vehicles: {len(vehicles)}\n"
            f"  - Clients: {len(clients)}\n"
            f"  - Duties: {len(duties)}\n"
            f"  - Invoices: {len(invoices)}\n"
            f"  - Operational Expenses: 50\n"
            f"  - Maintenance Records: 30\n"
            f"  - Vehicle Papers: 40\n"
            f"  - Driver Attendances: 80\n"
            f"  - Follow-up Records: 20\n"
            f"  - Custom Reminders: 15\n"
            f"  - Spare Parts Inventory: 10\n"
            f"  - Audit Events: 30\n"
        ))
