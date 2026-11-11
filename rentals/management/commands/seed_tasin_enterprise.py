from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rentals.models import (
    Organization, Branch, Membership, Party, Vehicle,
    VehicleDetail, PartyDetail, Duty, Garage, MaintenanceRecord,
    VehiclePaper, FinancialAccount, AccountTransaction, SystemAlert, CompanyProfile
)

D = Decimal

class Command(BaseCommand):
    help = 'Seeds TASIN ENTERPRISE complete data across all 15 modules.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='admin')
        parser.add_argument('--password', default='admin1234')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']

        User = get_user_model()
        user, created = User.objects.get_or_create(username=username, defaults={'email': 'admin@tasinenterprise.com', 'is_staff': True, 'is_superuser': True})
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f'Created user {username} with password {password}'))

        # 1. Organization & Company Profile
        org, _ = Organization.objects.get_or_create(name='TASIN ENTERPRISE')
        branch, _ = Branch.objects.get_or_create(organization=org, name='Main')
        Membership.objects.get_or_create(organization=org, user=user, defaults={'role': 'admin', 'branch': branch})

        CompanyProfile.objects.update_or_create(
            organization=org,
            defaults={
                'company_name': 'TASIN ENTERPRISE',
                'address': 'Level 8, Motijheel C/A, Dhaka-1000, Bangladesh',
                'phone': '+880 1711-234567',
                'email': 'info@tasinenterprise.com',
                'website': 'https://tasinenterprise.com',
                'bin': 'BIN-002918274-0101',
                'tin': 'TIN-992817263541',
                'details': 'TASIN ENTERPRISE – Premium Rent a Car Management Software',
                'currency': 'BDT',
                'timezone': 'Asia/Dhaka'
            }
        )

        # 2. Owners
        owners_data = [
            {'name': 'Farhana Rahman', 'mobile': '01711-234567', 'alt': '01911-234567', 'email': 'farhana@gmail.com', 'nid': '1234567890123', 'present': 'House 12, Road 5, Dhanmondi, Dhaka', 'perm': 'Village Badhair, Narail', 'bank': 'Dutch-Bangla Bank', 'acc_name': 'Farhana Rahman', 'acc_no': '2241001234567', 'branch': 'Dhanmondi Branch', 'payable': D('136000'), 'paid': D('90000')},
            {'name': 'Rashed Karim', 'mobile': '01811-345678', 'alt': '', 'email': 'rashed.karim@gmail.com', 'nid': '2345678901234', 'present': 'Apt 4B, Green Road, Dhaka', 'perm': 'Faridpur Sadar, Faridpur', 'bank': 'BRAC Bank', 'acc_name': 'Rashed Karim', 'acc_no': '3351002345678', 'branch': 'Farmgate Branch', 'payable': D('95000'), 'paid': D('50000')},
            {'name': 'Nadia Sultana', 'mobile': '01611-456789', 'alt': '01311-456789', 'email': 'nadia.sultana@yahoo.com', 'nid': '3456789012345', 'present': 'Block C, Mirpur-10, Dhaka', 'perm': 'Jamalpur Sadar, Jamalpur', 'bank': 'Islami Bank', 'acc_name': 'Nadia Sultana', 'acc_no': '2051003456789', 'branch': 'Mirpur Branch', 'payable': D('120000'), 'paid': D('65000')},
            {'name': 'Mahmudul Hasan', 'mobile': '01511-567890', 'alt': '', 'email': 'mahmud.hasan@outlook.com', 'nid': '4567890123456', 'present': 'House 8, Gulshan-2, Dhaka', 'perm': 'Comilla Sadar, Comilla', 'bank': 'Sonali Bank', 'acc_name': 'Mahmudul Hasan', 'acc_no': '0193004567890', 'branch': 'Gulshan Branch', 'payable': D('155000'), 'paid': D('70000')},
        ]
        owner_objs = {}
        for o in owners_data:
            p, _ = Party.objects.get_or_create(organization=org, kind=Party.Kind.OWNER, name=o['name'], defaults={'phone': o['mobile'], 'email': o['email']})
            PartyDetail.objects.update_or_create(
                party=p,
                defaults={
                    'alt_mobile': o['alt'], 'nid': o['nid'], 'present_address': o['present'], 'permanent_address': o['perm'],
                    'bank_name': o['bank'], 'account_name': o['acc_name'], 'account_number': o['acc_no'], 'branch_name': o['branch'],
                    'joining_date': date(2024, 1, 1), 'status': 'Active', 'total_vehicles': 2,
                    'total_payable_owner': o['payable'], 'total_paid_owner': o['paid']
                }
            )
            owner_objs[o['name']] = p

        # 3. Drivers
        drivers_data = [
            {'name': 'Imran Hossain', 'mobile': '01712-111222', 'alt': '01912-111222', 'nid': '5678901234567', 'present': 'Kalyanpur, Dhaka', 'dob': date(1988, 5, 15), 'license': 'B-234567-2019', 'exp': date(2027, 5, 14), 'emg': '01712-333444', 'salary': D('20000'), 'allow': D('300'), 'adv': D('5000'), 'paid': D('15000')},
            {'name': 'Hasan Ali', 'mobile': '01813-222333', 'alt': '', 'nid': '6789012345678', 'present': 'Mohammadpur, Dhaka', 'dob': date(1991, 8, 22), 'license': 'B-345678-2021', 'exp': date(2026, 8, 21), 'emg': '01813-444555', 'salary': D('18000'), 'allow': D('250'), 'adv': D('2000'), 'paid': D('18000')},
            {'name': 'Jamal Uddin', 'mobile': '01614-333444', 'alt': '', 'nid': '7890123456789', 'present': 'Jatrabari, Dhaka', 'dob': date(1985, 12, 1), 'license': 'B-456789-2018', 'exp': date(2026, 11, 30), 'emg': '01614-555666', 'salary': D('25000'), 'allow': D('400'), 'adv': D('8000'), 'paid': D('20000')},
            {'name': 'Sabbir Ahmed', 'mobile': '01515-444555', 'alt': '', 'nid': '8901234567890', 'present': 'Uttara, Dhaka', 'dob': date(1993, 3, 18), 'license': 'B-567890-2022', 'exp': date(2028, 3, 17), 'emg': '01515-666777', 'salary': D('22000'), 'allow': D('350'), 'adv': D('3000'), 'paid': D('22000')},
            {'name': 'Rafiqul Islam', 'mobile': '01716-555666', 'alt': '', 'nid': '9012345678901', 'present': 'Rayer Bazar, Dhaka', 'dob': date(1986, 7, 30), 'license': 'B-678901-2017', 'exp': date(2025, 7, 29), 'emg': '01716-777888', 'salary': D('20000'), 'allow': D('300'), 'adv': D('0'), 'paid': D('20000')},
            {'name': 'Kamal Hossain', 'mobile': '01817-666777', 'alt': '', 'nid': '0123456789012', 'present': 'Lalbagh, Dhaka', 'dob': date(1990, 11, 5), 'license': 'B-789012-2023', 'exp': date(2029, 11, 4), 'emg': '01817-888999', 'salary': D('18000'), 'allow': D('250'), 'adv': D('0'), 'paid': D('0')},
        ]
        driver_objs = {}
        for d in drivers_data:
            p, _ = Party.objects.get_or_create(organization=org, kind=Party.Kind.DRIVER, name=d['name'], defaults={'phone': d['mobile']})
            PartyDetail.objects.update_or_create(
                party=p,
                defaults={
                    'alt_mobile': d['alt'], 'nid': d['nid'], 'present_address': d['present'], 'dob': d['dob'],
                    'joining_date': date(2024, 1, 10), 'license_no': d['license'], 'license_expiry': d['exp'],
                    'emergency_contact': d['emg'], 'monthly_salary': d['salary'], 'daily_allowance': d['allow'],
                    'advance': d['adv'], 'total_paid_driver': d['paid'], 'status': 'Active'
                }
            )
            driver_objs[d['name']] = p

        # 4. Clients
        clients_data = [
            {'name': 'Meridian Textiles Ltd.', 'contact': 'Mr. Farhan Islam', 'phone': '01711-998877', 'email': 'farhan@meridiantex.com', 'addr': 'Level 8, Bashundhara City, Panthapath, Dhaka', 'trade': 'TL-2019-004512', 'type': 'Company', 'rec': D('100000'), 'col': D('70000')},
            {'name': 'Northstar Holdings', 'contact': 'Ms. Shahnaz Parveen', 'phone': '01812-887766', 'email': 'shahnaz@northstar.com.bd', 'addr': 'House 15, Gulshan-1, Dhaka', 'trade': 'TL-2020-001923', 'type': 'Company', 'rec': D('85000'), 'col': D('85000')},
            {'name': 'Apex Logistics', 'contact': 'Mr. Tanvir Rahman', 'phone': '01613-776655', 'email': 'tanvir@apexlogistics.bd', 'addr': 'Plot 22, BSCIC Industrial Area, Chittagong', 'trade': 'TL-2021-008271', 'type': 'Company', 'rec': D('130000'), 'col': D('90000')},
            {'name': 'Eastern Trading Co.', 'contact': 'Mr. Ashraf Ali', 'phone': '01514-665544', 'email': 'ashraf@easterntrading.com', 'addr': 'Block D, Motijheel, Dhaka', 'trade': 'TL-2018-003319', 'type': 'Company', 'rec': D('110000'), 'col': D('50000')},
            {'name': 'Prime Pharma Group', 'contact': 'Dr. Nasreen Akter', 'phone': '01715-554433', 'email': 'nasreen@primepharma.com.bd', 'addr': 'Tejgaon Industrial Area, Dhaka', 'trade': 'TL-2022-005511', 'type': 'Company', 'rec': D('95000'), 'col': D('95000')},
        ]
        client_objs = {}
        for c in clients_data:
            p, _ = Party.objects.get_or_create(organization=org, kind=Party.Kind.CUSTOMER, name=c['name'], defaults={'phone': c['phone'], 'email': c['email']})
            PartyDetail.objects.update_or_create(
                party=p,
                defaults={
                    'contact_person': c['contact'], 'present_address': c['addr'], 'billing_address': c['addr'],
                    'trade_license': c['trade'], 'client_type': c['type'], 'joining_date': date(2026, 1, 1),
                    'total_receivable_client': c['rec'], 'total_collected_client': c['col'], 'status': 'Active'
                }
            )
            client_objs[c['name']] = p

        # 5. Vehicles
        vehicles_data = [
            {'reg': 'DHA-METRO-GA-19-7724', 'brand': 'Toyota', 'model': 'Axio', 'year': 2019, 'cat': 'Sedan', 'color': 'White', 'chassis': 'NZE1419023451', 'eng': '1NZ-9023451', 'st': 'On Duty', 'owner': 'Farhana Rahman', 'driver': 'Imran Hossain', 'rent': D('60000')},
            {'reg': 'DHA-METRO-GA-22-4811', 'brand': 'Honda', 'model': 'Grace', 'year': 2022, 'cat': 'Sedan', 'color': 'Silver', 'chassis': 'GM48110049823', 'eng': 'LEB-0049823', 'st': 'On Duty', 'owner': 'Rashed Karim', 'driver': 'Hasan Ali', 'rent': D('50000')},
            {'reg': 'DHA-METRO-CHA-14-2056', 'brand': 'Toyota', 'model': 'Noah', 'year': 2014, 'cat': 'Microbus', 'color': 'Blue', 'chassis': 'ZRR80G0023912', 'eng': '3ZR-0023912', 'st': 'On Duty', 'owner': 'Farhana Rahman', 'driver': 'Jamal Uddin', 'rent': D('76000')},
            {'reg': 'DHA-METRO-GA-25-1190', 'brand': 'Mitsubishi', 'model': 'Xpander', 'year': 2025, 'cat': 'SUV', 'color': 'Black', 'chassis': 'GF8W0019012', 'eng': '4G93-0019012', 'st': 'On Duty', 'owner': 'Nadia Sultana', 'driver': 'Sabbir Ahmed', 'rent': D('65000')},
            {'reg': 'CTG-METRO-GA-21-3341', 'brand': 'Toyota', 'model': 'Corolla', 'year': 2021, 'cat': 'Sedan', 'color': 'Red', 'chassis': 'ZZE14209871', 'eng': '1ZZ-09871', 'st': 'Available', 'owner': 'Rashed Karim', 'driver': None, 'rent': D('45000')},
            {'reg': 'DHA-METRO-GA-18-9922', 'brand': 'Nissan', 'model': 'X-Trail', 'year': 2018, 'cat': 'SUV', 'color': 'Dark Gray', 'chassis': 'T32AA002213', 'eng': 'QR25-002213', 'st': 'In Garage', 'owner': 'Mahmudul Hasan', 'driver': 'Rafiqul Islam', 'rent': D('70000')},
            {'reg': 'DHA-METRO-GA-23-5566', 'brand': 'Toyota', 'model': 'Hiace', 'year': 2023, 'cat': 'Van', 'color': 'White', 'chassis': 'KDH2069981', 'eng': '1KD-9981', 'st': 'Available', 'owner': 'Mahmudul Hasan', 'driver': None, 'rent': D('85000')},
            {'reg': 'DHA-METRO-GA-20-7788', 'brand': 'Toyota', 'model': 'Premio', 'year': 2020, 'cat': 'Sedan', 'color': 'White', 'chassis': 'NZT2600123', 'eng': '1NZ-00123', 'st': 'Inactive', 'owner': 'Nadia Sultana', 'driver': None, 'rent': D('55000')},
        ]
        veh_objs = {}
        for v in vehicles_data:
            owner = owner_objs[v['owner']]
            veh, _ = Vehicle.objects.get_or_create(organization=org, registration=v['reg'], defaults={'model_name': f"{v['brand']} {v['model']}", 'owner': owner, 'branch': branch})
            driver = driver_objs.get(v['driver']) if v['driver'] else None
            VehicleDetail.objects.update_or_create(
                vehicle=veh,
                defaults={
                    'brand': v['brand'], 'model': v['model'], 'year': v['year'], 'category': v['cat'],
                    'color': v['color'], 'chassis_no': v['chassis'], 'engine_no': v['eng'], 'status': v['st'],
                    'monthly_rent': v['rent'], 'registration_date': date(v['year'], 1, 15),
                    'agreement_start': date(2026, 1, 1), 'assigned_driver': driver
                }
            )
            if driver:
                d_det, _ = PartyDetail.objects.get_or_create(party=driver)
                d_det.assigned_vehicle = veh
                d_det.save()
            veh_objs[v['reg']] = veh

            # Papers for this vehicle
            for p_type, days_ahead in [('fitness', 90), ('tax_token', 120), ('insurance', 45), ('route_permit', 180)]:
                VehiclePaper.objects.update_or_create(
                    organization=org, vehicle=veh, paper_type=p_type,
                    defaults={
                        'paper_number': f'{p_type.upper()}-{v["reg"][-4:]}',
                        'issue_date': date.today() - timedelta(days=200),
                        'expiry_date': date.today() + timedelta(days=days_ahead),
                        'status': 'Valid' if days_ahead > 30 else 'Expiring Soon'
                    }
                )

        # 6. Garages
        garages_data = [
            {'id': 'GRG-001', 'name': 'Star Auto Workshop', 'contact': 'Mr. Jalal Uddin', 'phone': '01711-123456', 'addr': 'Tejgaon Industrial Area, Dhaka', 'type': 'General', 'bill': D('85000'), 'paid': D('60000')},
            {'id': 'GRG-002', 'name': 'Cool AC Service Center', 'contact': 'Mr. Milon Ahmed', 'phone': '01811-234567', 'addr': 'Mirpur-10, Dhaka', 'type': 'AC Specialist', 'bill': D('32000'), 'paid': D('32000')},
            {'id': 'GRG-003', 'name': 'Premium Motors Ltd.', 'contact': 'Mr. Nurul Islam', 'phone': '01611-345678', 'addr': 'Mohakhali, Dhaka', 'type': 'Engine', 'bill': D('120000'), 'paid': D('80000')},
        ]
        garage_objs = {}
        for g in garages_data:
            grg, _ = Garage.objects.update_or_create(
                organization=org, garage_id=g['id'],
                defaults={'name': g['name'], 'contact_person': g['contact'], 'mobile': g['phone'], 'address': g['addr'], 'garage_type': g['type'], 'total_bill': g['bill'], 'total_paid': g['paid']}
            )
            garage_objs[g['name']] = grg

        # 7. Maintenance Records
        MaintenanceRecord.objects.update_or_create(
            organization=org, maintenance_id='MNT-001',
            defaults={
                'vehicle': veh_objs['DHA-METRO-GA-19-7724'], 'vehicle_reg': 'DHA-METRO-GA-19-7724',
                'garage': garage_objs['Star Auto Workshop'], 'garage_name': 'Star Auto Workshop',
                'date': date(2026, 9, 12), 'problem_description': 'Brake pads worn, brake fluid low',
                'service_type': 'Brake', 'parts_details': 'Front brake pads x 2, brake fluid 1L',
                'labour_cost': D('3000'), 'parts_cost': D('5000'), 'total_cost': D('8000'),
                'expected_delivery': date(2026, 9, 13), 'actual_delivery': date(2026, 9, 13), 'status': 'Delivered'
            }
        )
        MaintenanceRecord.objects.update_or_create(
            organization=org, maintenance_id='MNT-002',
            defaults={
                'vehicle': veh_objs['DHA-METRO-GA-25-1190'], 'vehicle_reg': 'DHA-METRO-GA-25-1190',
                'garage': garage_objs['Cool AC Service Center'], 'garage_name': 'Cool AC Service Center',
                'date': date(2026, 9, 17), 'problem_description': 'AC not cooling properly',
                'service_type': 'AC', 'parts_details': 'AC Gas recharge, compressor service',
                'labour_cost': D('2000'), 'parts_cost': D('4200'), 'total_cost': D('6200'),
                'expected_delivery': date(2026, 9, 18), 'status': 'In Progress'
            }
        )
        MaintenanceRecord.objects.update_or_create(
            organization=org, maintenance_id='MNT-003',
            defaults={
                'vehicle': veh_objs['DHA-METRO-GA-18-9922'], 'vehicle_reg': 'DHA-METRO-GA-18-9922',
                'garage': garage_objs['Premium Motors Ltd.'], 'garage_name': 'Premium Motors Ltd.',
                'date': date(2026, 11, 5), 'problem_description': 'Engine overheating, coolant leak detected',
                'service_type': 'Engine', 'parts_details': 'Coolant hose, thermostat',
                'labour_cost': D('8000'), 'parts_cost': D('12000'), 'total_cost': D('20000'),
                'expected_delivery': date(2026, 11, 12), 'status': 'In Progress'
            }
        )

        # 8. Daily Duties
        Duty.objects.update_or_create(
            organization=org, duty_id='DTY-001',
            defaults={
                'date': date.today(), 'client': client_objs['Meridian Textiles Ltd.'], 'client_name': 'Meridian Textiles Ltd.',
                'vehicle': veh_objs['DHA-METRO-GA-19-7724'], 'vehicle_reg': 'DHA-METRO-GA-19-7724',
                'driver': driver_objs['Imran Hossain'], 'driver_name': 'Imran Hossain',
                'pickup_location': 'Panthapath, Dhaka', 'destination': 'Narayanganj Export Zone',
                'start_time': '08:00', 'end_time': '17:00', 'duty_type': 'Monthly', 'customer_contact': '01711-998877',
                'description': 'Factory regular duty', 'agreed_rent': D('100000'), 'fuel_cost': D('0'), 'overtime_charge': D('0'),
                'driver_allowance': D('300'), 'additional_charge': D('0'), 'discount': D('0'), 'total_amount': D('100300'), 'status': 'Running'
            }
        )
        Duty.objects.update_or_create(
            organization=org, duty_id='DTY-002',
            defaults={
                'date': date.today(), 'client': client_objs['Northstar Holdings'], 'client_name': 'Northstar Holdings',
                'vehicle': veh_objs['DHA-METRO-GA-22-4811'], 'vehicle_reg': 'DHA-METRO-GA-22-4811',
                'driver': driver_objs['Hasan Ali'], 'driver_name': 'Hasan Ali',
                'pickup_location': 'Gulshan-1, Dhaka', 'destination': 'Airport Road',
                'start_time': '09:00', 'end_time': '18:00', 'duty_type': 'Monthly', 'customer_contact': '01812-887766',
                'description': 'Corporate transfer', 'agreed_rent': D('85000'), 'fuel_cost': D('2500'), 'overtime_charge': D('0'),
                'driver_allowance': D('250'), 'additional_charge': D('0'), 'discount': D('0'), 'total_amount': D('87750'), 'status': 'Running'
            }
        )
        Duty.objects.update_or_create(
            organization=org, duty_id='DTY-003',
            defaults={
                'date': date.today(), 'client': client_objs['Apex Logistics'], 'client_name': 'Apex Logistics',
                'vehicle': veh_objs['DHA-METRO-CHA-14-2056'], 'vehicle_reg': 'DHA-METRO-CHA-14-2056',
                'driver': driver_objs['Jamal Uddin'], 'driver_name': 'Jamal Uddin',
                'pickup_location': 'Chittagong Port', 'destination': 'Dhaka EPZ',
                'start_time': '06:00', 'end_time': '20:00', 'duty_type': 'Trip', 'customer_contact': '01613-776655',
                'description': 'Outstation goods pickup', 'agreed_rent': D('15000'), 'fuel_cost': D('5000'), 'overtime_charge': D('2000'),
                'driver_allowance': D('800'), 'additional_charge': D('500'), 'discount': D('0'), 'total_amount': D('23300'), 'status': 'Completed'
            }
        )
        Duty.objects.update_or_create(
            organization=org, duty_id='DTY-004',
            defaults={
                'date': date.today(), 'client': client_objs['Eastern Trading Co.'], 'client_name': 'Eastern Trading Co.',
                'vehicle': veh_objs['DHA-METRO-GA-25-1190'], 'vehicle_reg': 'DHA-METRO-GA-25-1190',
                'driver': driver_objs['Sabbir Ahmed'], 'driver_name': 'Sabbir Ahmed',
                'pickup_location': 'Motijheel, Dhaka', 'destination': 'Narsingdi',
                'start_time': '10:00', 'end_time': '16:00', 'duty_type': 'Daily', 'customer_contact': '01514-665544',
                'description': 'Day trip', 'agreed_rent': D('5000'), 'fuel_cost': D('1500'), 'overtime_charge': D('0'),
                'driver_allowance': D('350'), 'additional_charge': D('0'), 'discount': D('0'), 'total_amount': D('6850'), 'status': 'Scheduled'
            }
        )

        # 9. Accounts & Balances
        acc1, _ = FinancialAccount.objects.update_or_create(organization=org, account_id='ACC-001', defaults={'name': 'Main Cash Account', 'account_type': 'Cash', 'opening_balance': D('500000'), 'current_balance': D('283200')})
        acc2, _ = FinancialAccount.objects.update_or_create(organization=org, account_id='ACC-002', defaults={'name': 'Dutch-Bangla Bank', 'account_number': '2241001234567', 'account_type': 'Bank', 'bank_name': 'Dutch-Bangla Bank Ltd.', 'opening_balance': D('1000000'), 'current_balance': D('756800')})
        acc3, _ = FinancialAccount.objects.update_or_create(organization=org, account_id='ACC-003', defaults={'name': 'bKash Business', 'account_type': 'Mobile Banking', 'opening_balance': D('50000'), 'current_balance': D('32500')})
        acc4, _ = FinancialAccount.objects.update_or_create(organization=org, account_id='ACC-004', defaults={'name': 'BRAC Bank', 'account_number': '3351002345678', 'account_type': 'Bank', 'bank_name': 'BRAC Bank Ltd.', 'opening_balance': D('500000'), 'current_balance': D('423000')})

        # 10. Transactions
        txns_data = [
            {'id': 'TXN-001', 'date': date.today(), 'type': 'Expense', 'cat': 'Fuel', 'amount': D('2500'), 'person': 'Padma Filling Station', 'veh': 'DHA-METRO-GA-22-4811', 'method': 'Cash', 'ref': 'FUEL-001', 'desc': 'Fuel for VEH-002'},
            {'id': 'TXN-002', 'date': date.today() - timedelta(days=1), 'type': 'Expense', 'cat': 'Driver Food/Allowance', 'amount': D('800'), 'person': 'Jamal Uddin', 'veh': 'DHA-METRO-CHA-14-2056', 'method': 'Mobile Banking', 'ref': 'BKS-291', 'desc': 'Driver allowance - Jamal Uddin'},
            {'id': 'TXN-003', 'date': date.today() - timedelta(days=2), 'type': 'Expense', 'cat': 'Toll', 'amount': D('500'), 'person': 'Highway Authority', 'veh': 'DHA-METRO-CHA-14-2056', 'method': 'Cash', 'ref': '', 'desc': 'Dhaka-Chittagong highway toll'},
            {'id': 'TXN-004', 'date': date.today() - timedelta(days=3), 'type': 'Expense', 'cat': 'Office Rent', 'amount': D('25000'), 'person': 'Building Owner', 'veh': '', 'method': 'Bank Transfer', 'ref': 'BEFTN-7001', 'desc': 'Office rent - November'},
            {'id': 'TXN-005', 'date': date.today() - timedelta(days=5), 'type': 'Expense', 'cat': 'Maintenance', 'amount': D('20000'), 'person': 'Premium Motors Ltd.', 'veh': 'DHA-METRO-GA-18-9922', 'method': 'Bank Transfer', 'ref': 'MNT-003', 'desc': 'VEH-006 Engine repair'},
            {'id': 'TXN-006', 'date': date.today(), 'type': 'Income', 'cat': 'Customer Collection', 'amount': D('70000'), 'person': 'Meridian Textiles Ltd.', 'veh': '', 'method': 'Bank Transfer', 'ref': 'CHQ-8901', 'desc': 'Monthly billing collection'},
            {'id': 'TXN-007', 'date': date.today() - timedelta(days=1), 'type': 'Income', 'cat': 'Customer Collection', 'amount': D('85000'), 'person': 'Northstar Holdings', 'veh': '', 'method': 'Bank Transfer', 'ref': 'BEFTN-4421', 'desc': 'Full payment received'},
        ]
        for t in txns_data:
            AccountTransaction.objects.update_or_create(
                organization=org, transaction_id=t['id'],
                defaults={
                    'date': t['date'], 'transaction_type': t['type'], 'category': t['cat'],
                    'amount': t['amount'], 'related_person': t['person'], 'vehicle_reg': t['veh'],
                    'payment_method': t['method'], 'reference': t['ref'], 'description': t['desc']
                }
            )

        # 11. System Alerts
        alerts_data = [
            {'id': 'ALT-001', 'type': 'payment', 'sev': 'danger', 'title': 'Eastern Trading Co. – Payment Overdue', 'detail': 'Invoice amount ৳60,000 overdue by 15 days', 'due': date.today() - timedelta(days=15), 'rel': 'CLT-004'},
            {'id': 'ALT-002', 'type': 'document', 'sev': 'warning', 'title': 'Driver License Expiring Soon – Rafiqul Islam', 'detail': 'License expires on 2025-07-29', 'due': date(2025, 7, 29), 'rel': 'DRV-005'},
            {'id': 'ALT-003', 'type': 'agreement', 'sev': 'info', 'title': 'Agreement Renewal Due – Apex Logistics', 'detail': 'Vehicle DHA-METRO-CHA-14-2056 agreement ending', 'due': date.today() + timedelta(days=3), 'rel': 'CLT-003'},
            {'id': 'ALT-004', 'type': 'maintenance', 'sev': 'warning', 'title': 'VEH-006 Still in Garage', 'detail': 'Expected delivery was 2026-11-12, still In Progress', 'due': date(2026, 11, 12), 'rel': 'MNT-003'},
            {'id': 'ALT-005', 'type': 'payment', 'sev': 'info', 'title': 'Owner Payment Due – Mahmudul Hasan', 'detail': 'Monthly rent for November not yet paid', 'due': date.today() + timedelta(days=5), 'rel': 'OWN-004'},
        ]
        for a in alerts_data:
            SystemAlert.objects.update_or_create(
                organization=org, alert_id=a['id'],
                defaults={
                    'alert_type': a['type'], 'severity': a['sev'], 'title': a['title'],
                    'detail': a['detail'], 'date': date.today(), 'due_date': a['due'],
                    'related_id': a['rel'], 'is_read': False
                }
            )

        self.stdout.write(self.style.SUCCESS(f'Successfully seeded TASIN ENTERPRISE complete data across all 15 modules! Organization ID: {org.pk}'))

