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
    TasinInvoice, TasinInvoiceItem, TasinInvoicePayment
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
        db_engine = settings.DATABASES['default'].get('ENGINE', '')
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
            # 1. Organization & Admin
            User = get_user_model()
            admin_user, _ = User.objects.get_or_create(
                username='tasin_admin',
                defaults={'email': 'admin@tasinenterprise.com', 'is_staff': True, 'is_superuser': True}
            )
            admin_user.set_password('TasinAdmin2026!')
            admin_user.save()

            org, org_created = Organization.objects.get_or_create(name=org_name)
            branch, _ = Branch.objects.get_or_create(organization=org, name='Dhaka Central')
            Membership.objects.get_or_create(organization=org, user=admin_user, defaults={'role': 'admin', 'branch': branch})

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

            # 2. Financial Accounts (4 accounts)
            acc_data = [
                ('ACC-001', 'Main Cash Account', 'Cash', '', 'Cash In Hand', 'Head Office', D('250000')),
                ('ACC-002', 'DBBL Current Account', 'Bank', '110.120.33445', 'Dutch-Bangla Bank', 'Motijheel Branch', D('1200000')),
                ('ACC-003', 'BRAC Bank Corporate', 'Bank', '150.230.99881', 'BRAC Bank', 'Gulshan Branch', D('850000')),
                ('ACC-004', 'bKash Merchant Account', 'Mobile Banking', '01711-000999', 'bKash', 'Corporate Division', D('75000')),
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

            # 3. Garages (3 garages)
            garage_data = [
                ('GRG-001', 'Rahman Auto Works', 'AC & Engine Specialist', 'Mokhlesur Rahman', '01712-111222', 'Tejgaon I/A, Dhaka', 'Engine', D('85000'), D('45000')),
                ('GRG-002', 'Bengal Motor Care', 'General & Suspension', 'Belal Hossain', '01812-333444', 'Mirpur 10, Dhaka', 'General', D('60000'), D('35000')),
                ('GRG-003', 'Apex Body & Paint', 'Denting, Painting & Body', 'Shafiqul Alam', '01912-555666', 'Kakrail, Dhaka', 'Body Works', D('40000'), D('20000')),
            ]
            garages = {}
            for gid, name, note, contact, mobile, addr, gtype, bill, paid in garage_data:
                g, _ = Garage.objects.update_or_create(
                    organization=org, garage_id=gid,
                    defaults={
                        'name': name, 'contact_person': contact, 'mobile': mobile,
                        'address': addr, 'garage_type': gtype, 'status': 'Active',
                        'total_bill': bill, 'total_paid': paid
                    }
                )
                garages[name] = g

            # 4. Owners (5 owners)
            owner_data = [
                ('Farhana Rahman', '01711-234567', 'farhana@tasin.com', 'Dhanmondi, Dhaka', 'Dutch-Bangla Bank', '2241001234', D('150000'), D('100000')),
                ('Rashed Karim', '01811-345678', 'rashed@tasin.com', 'Green Road, Dhaka', 'BRAC Bank', '3351002345', D('100000'), D('60000')),
                ('Nadia Sultana', '01611-456789', 'nadia@tasin.com', 'Mirpur-10, Dhaka', 'Islami Bank', '2051003456', D('120000'), D('80000')),
                ('Mahmudul Hasan', '01511-567890', 'mahmud@tasin.com', 'Gulshan-2, Dhaka', 'Sonali Bank', '0193004567', D('180000'), D('120000')),
                ('Tariqul Islam', '01911-678901', 'tariqul@tasin.com', 'Uttara Sector 4, Dhaka', 'Eastern Bank', '1041005678', D('90000'), D('50000')),
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

            # 5. Drivers (8 drivers)
            driver_data = [
                ('Kamal Hossain', '01722-111111', 'DL-DH-10291', date.today() + timedelta(days=180), D('22000'), D('3000'), D('18000')),
                ('Hasan Ali', '01822-222222', 'DL-DH-20392', date.today() - timedelta(days=10), D('20000'), D('0'), D('15000')), # Expired
                ('Sabbir Ahmed', '01922-333333', 'DL-DH-30493', date.today() + timedelta(days=5), D('24000'), D('2000'), D('20000')),  # Expiring 5d
                ('Jamal Uddin', '01622-444444', 'DL-DH-40594', date.today() + timedelta(days=14), D('21000'), D('0'), D('17000')), # Expiring 14d
                ('Rafiqul Islam', '01522-555555', 'DL-DH-50695', date.today() + timedelta(days=28), D('23000'), D('1500'), D('19000')), # Expiring 28d
                ('Anwar Hossain', '01733-666666', 'DL-DH-60796', date.today() + timedelta(days=300), D('20000'), D('0'), D('16000')),
                ('Mizanur Rahman', '01833-777777', 'DL-DH-70897', date.today() + timedelta(days=240), D('25000'), D('4000'), D('20000')),
                ('Shafiqul Islam', '01933-888888', 'DL-DH-80998', date.today() + timedelta(days=90), D('21000'), D('0'), D('18000')),
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

            # 6. Vehicles (12 vehicles)
            vehicle_data = [
                ('DHA-METRO-GA-19-7724', 'Toyota', 'Premio', 2021, 'Sedan', 'Available', D('55000'), owners[0], drivers[0]),
                ('DHA-METRO-GA-22-4811', 'Toyota', 'Allion', 2020, 'Sedan', 'On Duty', D('50000'), owners[1], drivers[1]),
                ('DHA-METRO-CHA-15-3390', 'Toyota', 'Noah', 2019, 'Microbus', 'On Duty', D('65000'), owners[2], drivers[2]),
                ('DHA-METRO-GA-25-1190', 'Honda', 'Grace', 2022, 'Sedan', 'On Duty', D('52000'), owners[3], drivers[3]),
                ('DHA-METRO-GHA-11-8844', 'Toyota', 'HiAce', 2020, 'Microbus', 'Available', D('70000'), owners[4], drivers[4]),
                ('DHA-METRO-GA-18-9922', 'Toyota', 'Corolla Cross', 2023, 'SUV', 'In Garage', D('75000'), owners[0], drivers[5]),
                ('DHA-METRO-BA-12-3456', 'Mitsubishi', 'Pajero Sport', 2021, 'SUV', 'Available', D('90000'), owners[1], drivers[6]),
                ('DHA-METRO-GA-31-4455', 'Nissan', 'X-Trail', 2022, 'SUV', 'Available', D('80000'), owners[2], drivers[7]),
                ('DHA-METRO-CHA-53-1200', 'Toyota', 'Esquire', 2021, 'Microbus', 'On Duty', D('68000'), owners[3], drivers[0]),
                ('DHA-METRO-GA-29-8800', 'Toyota', 'Axio', 2019, 'Sedan', 'Available', D('45000'), owners[4], drivers[1]),
                ('DHA-METRO-GA-14-5511', 'Toyota', 'Fielder', 2018, 'Sedan', 'Inactive', D('42000'), owners[0], drivers[2]),
                ('DHA-METRO-GA-33-9911', 'Hyundai', 'Tucson', 2023, 'SUV', 'Inactive', D('85000'), owners[1], drivers[3]),
            ]
            vehicles = []
            for reg, brand, model, yr, cat, st, rent, owner, driver in vehicle_data:
                v, _ = Vehicle.objects.get_or_create(
                    organization=org, registration=reg,
                    defaults={'model_name': model, 'owner': owner}
                )
                VehicleDetail.objects.update_or_create(
                    vehicle=v,
                    defaults={
                        'brand': brand, 'model': model, 'year': yr, 'category': cat,
                        'status': st, 'monthly_rent': rent, 'assigned_driver': driver,
                        'mobil_count': 3, 'last_mobil_date': date.today() - timedelta(days=25)
                    }
                )
                vehicles.append(v)

            # 7. Vehicle Documents with staggered expiries (15 records)
            doc_specs = [
                (vehicles[0], 'fitness', 'FIT-2025-01', date.today() + timedelta(days=120)),
                (vehicles[0], 'tax_token', 'TAX-2025-01', date.today() + timedelta(days=60)),
                (vehicles[1], 'insurance', 'INS-2025-02', date.today() - timedelta(days=5)),   # Expired
                (vehicles[1], 'fitness', 'FIT-2025-02', date.today() + timedelta(days=4)),     # 4 days left
                (vehicles[2], 'tax_token', 'TAX-2025-03', date.today() + timedelta(days=12)),  # 12 days left
                (vehicles[2], 'insurance', 'INS-2025-03', date.today() + timedelta(days=25)),  # 25 days left
                (vehicles[3], 'fitness', 'FIT-2025-04', date.today() + timedelta(days=200)),
                (vehicles[4], 'tax_token', 'TAX-2025-05', date.today() - timedelta(days=15)),  # Expired
                (vehicles[5], 'insurance', 'INS-2025-06', date.today() + timedelta(days=7)),   # 7 days left
                (vehicles[6], 'fitness', 'FIT-2025-07', date.today() + timedelta(days=45)),
                (vehicles[7], 'tax_token', 'TAX-2025-08', date.today() + timedelta(days=90)),
                (vehicles[8], 'fitness', 'FIT-2025-09', date.today() + timedelta(days=150)),
                (vehicles[9], 'insurance', 'INS-2025-10', date.today() + timedelta(days=18)),
                (vehicles[10], 'tax_token', 'TAX-2025-11', date.today() + timedelta(days=300)),
                (vehicles[11], 'fitness', 'FIT-2025-12', date.today() + timedelta(days=250)),
            ]
            for veh, ptype, pnum, exp in doc_specs:
                VehiclePaper.objects.update_or_create(
                    organization=org, vehicle=veh, paper_type=ptype,
                    defaults={'paper_number': pnum, 'expiry_date': exp, 'issue_date': exp - timedelta(days=365)}
                )

            # 8. Clients (10 clients)
            client_data = [
                ('Meridian Textiles Ltd.', '01711-998877', 'info@meridiantextiles.com', 'Corporate Officer: Zahir Raihan', 'Company', D('185000'), D('125000')),
                ('Eastern Trading Co.', '01811-887766', 'procure@easterntrading.bd', 'Manager: Ashfaqul Huq', 'Company', D('120000'), D('90000')),
                ('Apex Logistics Ltd.', '01911-776655', 'transport@apexlogistics.com', 'Dispatch Lead: Tanvir Ahmed', 'Company', D('95000'), D('55000')),
                ('Square Pharmaceuticals', '01611-665544', 'admin@squarepharma.com', 'Operations: Mustafizur Rahman', 'Company', D('240000'), D('180000')),
                ('Beximco Communications', '01511-554433', 'fleet@beximco.net', 'Fleet Lead: Kamrul Islam', 'Company', D('160000'), D('110000')),
                ('Ministry of Foreign Affairs', '01722-443322', 'protocol@mofa.gov.bd', 'Protocol Officer: Masud Karim', 'Government', D('140000'), D('100000')),
                ('Standard Chartered Bank', '01822-332211', 'facilities@sc.com', 'Facilities: Nadia Khan', 'Company', D('210000'), D('170000')),
                ('Dr. Rezaul Karim', '01922-221100', 'dr.rezaul@gmail.com', 'Personal Lease', 'Individual', D('60000'), D('50000')),
                ('Engr. Salman Farhad', '01622-110099', 'salman.farhad@yahoo.com', 'Long Term Rental', 'Individual', D('75000'), D('45000')),
                ('Dhaka Power Distribution Co.', '01522-009988', 'transport@dpdc.org.bd', 'Transport Officer: Anwar Sadat', 'Government', D('130000'), D('90000')),
            ]
            clients = []
            for name, mobile, email, contact, ctype, rec, col in client_data:
                c, _ = Party.objects.get_or_create(organization=org, name=name, kind=Party.Kind.CUSTOMER, defaults={'phone': mobile, 'email': email})
                PartyDetail.objects.update_or_create(
                    party=c,
                    defaults={
                        'contact_person': contact, 'client_type': ctype,
                        'total_receivable_client': rec, 'total_collected_client': col,
                        'status': 'Active'
                    }
                )
                clients.append(c)

            # 9. Duties (30 historical duties across 3 months)
            today = date.today()
            duty_records = []
            for i in range(1, 31):
                duty_date = today - timedelta(days=(30 - i) * 2)
                c_idx = (i - 1) % len(clients)
                v_idx = (i - 1) % len(vehicles)
                d_idx = (i - 1) % len(drivers)

                client = clients[c_idx]
                vehicle = vehicles[v_idx]
                driver = drivers[d_idx]

                rent = D(3500 + (i % 5) * 500)
                fuel = D(1000 + (i % 3) * 300)
                allowance = D(500)
                total = rent + fuel + allowance
                st = 'Completed' if i <= 24 else ('Running' if i <= 28 else 'Scheduled')

                duty_id = f"DTY-{i:03d}"
                d, _ = Duty.objects.update_or_create(
                    organization=org, duty_id=duty_id,
                    defaults={
                        'date': duty_date, 'client': client, 'client_name': client.name,
                        'vehicle': vehicle, 'vehicle_reg': vehicle.registration,
                        'driver': driver, 'driver_name': driver.name,
                        'pickup_location': 'Uttara, Dhaka', 'destination': 'Gulshan-2 / Motijheel, Dhaka',
                        'start_time': '08:30 AM', 'end_time': '06:00 PM',
                        'duty_type': 'Daily', 'agreed_rent': rent, 'fuel_cost': fuel,
                        'driver_allowance': allowance, 'total_amount': total,
                        'status': st, 'is_invoiced': False
                    }
                )
                duty_records.append(d)

            # 10. Invoices (20 Invoices linked to completed duties)
            for inv_i in range(1, 21):
                inv_duty = duty_records[inv_i - 1]
                client = inv_duty.client
                inv_num = f"INV-2026-{inv_i:04d}"
                issue_d = inv_duty.date
                due_d = issue_d + timedelta(days=15)
                tot = inv_duty.total_amount
                paid_amt = tot if inv_i <= 10 else (tot * D('0.5') if inv_i <= 16 else D(0))
                st = 'Paid' if paid_amt >= tot else ('Partially Paid' if paid_amt > 0 else 'Sent')

                inv, _ = TasinInvoice.objects.update_or_create(
                    organization=org, invoice_number=inv_num,
                    defaults={
                        'client': client, 'client_name': client.name,
                        'issue_date': issue_d, 'due_date': due_d,
                        'subtotal': tot, 'total_amount': tot,
                        'paid_amount': paid_amt, 'status': st,
                        'notes': f"Invoice for official corporate transport on {issue_d}"
                    }
                )
                inv_duty.is_invoiced = True
                inv_duty.invoice = inv
                inv_duty.save()

                TasinInvoiceItem.objects.update_or_create(
                    invoice=inv, duty=inv_duty,
                    defaults={
                        'description': f"Duty {inv_duty.duty_id} on {inv_duty.date}: {inv_duty.vehicle_reg}",
                        'quantity': D(1), 'unit_price': tot, 'total_price': tot
                    }
                )

                if paid_amt > 0:
                    TasinInvoicePayment.objects.update_or_create(
                        invoice=inv, payment_id=f"PAY-{inv.invoice_number}-01",
                        defaults={
                            'date': issue_d + timedelta(days=5),
                            'amount': paid_amt,
                            'payment_method': 'Bank Transfer',
                            'account': accounts['ACC-002'],
                            'reference': f"CHK-{(88000 + inv_i)}",
                            'notes': 'Settled against invoice'
                        }
                    )

            # 11. Maintenance Records (12 records)
            maint_specs = [
                (vehicles[0], 'Rahman Auto Works', 'Oil Change', D('1500'), D('4500'), 'Completed'),
                (vehicles[1], 'Bengal Motor Care', 'Brake', D('2000'), D('3500'), 'Completed'),
                (vehicles[2], 'Apex Body & Paint', 'AC', D('2500'), D('5000'), 'Completed'),
                (vehicles[3], 'Rahman Auto Works', 'General Service', D('3000'), D('6000'), 'Completed'),
                (vehicles[4], 'Bengal Motor Care', 'Suspension', D('4000'), D('8000'), 'Completed'),
                (vehicles[5], 'Rahman Auto Works', 'Engine', D('8000'), D('18000'), 'In Progress'),
                (vehicles[6], 'Apex Body & Paint', 'Tyre', D('1000'), D('12000'), 'Completed'),
                (vehicles[7], 'Rahman Auto Works', 'Oil Change', D('1500'), D('4500'), 'Completed'),
                (vehicles[8], 'Bengal Motor Care', 'Battery', D('1000'), D('7500'), 'Completed'),
                (vehicles[9], 'Apex Body & Paint', 'Electrical', D('2000'), D('3000'), 'Completed'),
                (vehicles[10], 'Rahman Auto Works', 'Gearbox', D('5000'), D('11000'), 'Completed'),
                (vehicles[11], 'Bengal Motor Care', 'General Service', D('2500'), D('4500'), 'Pending'),
            ]
            for m_i, (veh, g_name, stype, labour, parts, mst) in enumerate(maint_specs, 1):
                MaintenanceRecord.objects.update_or_create(
                    organization=org, maintenance_id=f"MNT-{m_i:03d}",
                    defaults={
                        'vehicle': veh, 'vehicle_reg': veh.registration,
                        'garage_name': g_name, 'date': today - timedelta(days=m_i * 6),
                        'problem_description': f"Scheduled maintenance service: {stype}",
                        'service_type': stype, 'labour_cost': labour, 'parts_cost': parts,
                        'total_cost': labour + parts, 'status': mst
                    }
                )

            # 12. Expenses (25 records across historical dates)
            exp_categories = ['Fuel', 'Mobil/Lubricant', 'Office Rent', 'Staff Salary', 'Toll & Parking', 'Vehicle Repair', 'Utilities']
            for e_i in range(1, 26):
                cat = exp_categories[(e_i - 1) % len(exp_categories)]
                e_date = today - timedelta(days=e_i * 3)
                v_reg = vehicles[(e_i - 1) % len(vehicles)].registration if 'Fuel' in cat or 'Vehicle' in cat or 'Mobil' in cat else ''
                amt = D(1200 + (e_i % 7) * 450)
                AccountTransaction.objects.update_or_create(
                    organization=org, transaction_id=f"EXP-2026-{e_i:03d}",
                    defaults={
                        'date': e_date, 'transaction_type': 'Expense',
                        'category': cat, 'amount': amt, 'payment_method': 'Cash',
                        'vehicle_reg': v_reg, 'reference': f"VCH-{1000 + e_i}",
                        'description': f"Expense for {cat} on {e_date}"
                    }
                )

        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded TASIN ENTERPRISE synthetic data:\n"
            f"  - 5 Owners\n"
            f"  - 12 Vehicles\n"
            f"  - 8 Drivers\n"
            f"  - 10 Clients\n"
            f"  - 3 Garages\n"
            f"  - 4 Financial Accounts\n"
            f"  - 30 Duties (Historical across 3 months)\n"
            f"  - 20 Invoices with Line Items & Payments\n"
            f"  - 12 Maintenance Records\n"
            f"  - 15 Vehicle Papers with Staggered Expiries\n"
            f"  - 25 Expenses\n"
            f"  - Fully interconnected ledger transactions"
        ))
