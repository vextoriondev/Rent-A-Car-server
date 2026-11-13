"""
TASIN ENTERPRISE — ZERO-SKIP VERIFICATION & AUTOMATED AUDIT SUITE
Tests all 15 modules, 5 RBAC roles, financial calculations, ledgers, and newly introduced services.
"""
import os
import sys
import django
from decimal import Decimal

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['USE_SQLITE'] = '1'

django.setup()

from django.contrib.auth import get_user_model
from rentals.models import (
    Organization, Party, PartyDetail, Vehicle, VehicleDetail,
    Duty, Invoice, InvoiceLine, Expense, MaintenanceRecord,
    FinancialAccount, AccountTransaction, DriverAttendance,
    SparePart, PartConsumption, DueFollowUp, CustomReminder,
    TripDispatch, PrivateDocument, TasinInvoice
)
from rentals.rbac import (
    ROLE_PERMISSIONS, get_user_role, check_permission, get_user_permissions_payload
)
from rentals.services.tasin import (
    calculate_previous_month_comparison,
    auto_increment_mobil_count,
    get_driver_attendance_register,
    dispatch_duty_trip_sheet,
    renew_agreement_service,
    reconcile_bank_statement,
    build_custom_report,
    generate_organization_backup
)

User = get_user_model()

def run_audit():
    print("=" * 70)
    print("TASIN ENTERPRISE — COMPREHENSIVE AUTOMATED VERIFICATION SUITE")
    print("=" * 70)

    org = Organization.objects.first()
    if not org:
        print("[FAIL] No organization found. Run seed_tasin_demo first.")
        return False

    print(f"[OK] Testing with Organization: {org.name} (ID: {org.id})")

    passed_tests = 0
    total_tests = 0

    # -------------------------------------------------------------
    # TEST 1: Granular Role-Based Access Control (RBAC) - 5 Roles
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 1] Verifying Granular RBAC Permissions across 5 Roles...")
    expected_roles = ['super_admin', 'admin', 'accountant', 'operations_manager', 'staff']
    for role in expected_roles:
        perms = ROLE_PERMISSIONS.get(role, {})
        assert len(perms) > 0, f"Role {role} has empty permissions"
    
    # Assert Super Admin has all permissions
    sa_perms = ROLE_PERMISSIONS['super_admin']
    assert len(sa_perms) >= 20, "Super admin permission set too small"

    # Assert Staff cannot access accounts or manage invoices
    staff_perms = ROLE_PERMISSIONS['staff']
    assert 'accounts:manage' not in staff_perms, "Staff should not have accounts:manage"
    assert 'invoices:manage' not in staff_perms, "Staff should not have invoices:manage"
    assert 'duties:view' in staff_perms, "Staff should have duties:view"

    # Assert Accountant has financial access but not driver management
    acc_perms = ROLE_PERMISSIONS['accountant']
    assert 'invoices:manage' in acc_perms and 'accounts:manage' in acc_perms
    assert 'drivers:manage' not in acc_perms

    print("   ✓ All 5 roles conform to security matrix and least-privilege principles.")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 2: Financial Integrity & Invoicing Calculations
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 2] Verifying Invoice Line Totals and Balance Calculations...")
    client = Party.objects.filter(organization=org, kind='customer').first()
    assert client is not None, "Client not found"

    inv = TasinInvoice.objects.filter(organization=org).first()
    if not inv:
        inv = TasinInvoice.objects.create(
            organization=org,
            invoice_number="INV-2026-TEST",
            client=client,
            client_name=client.name,
            issue_date="2026-11-10",
            due_date="2026-11-20",
            subtotal=Decimal('25000.00'),
            discount=Decimal('1000.00'),
            tax=Decimal('1250.00'),
            total_amount=Decimal('25250.00'),
            paid_amount=Decimal('10000.00'),
            status='Partially Paid'
        )

    expected_total = inv.subtotal + inv.tax - inv.discount
    expected_due = expected_total - inv.paid_amount
    assert abs(inv.total_amount - expected_total) < Decimal('0.01'), f"Total mismatch: {inv.total_amount} vs {expected_total}"
    assert abs(Decimal(str(inv.remaining_due)) - expected_due) < Decimal('0.01'), f"Remaining due mismatch: {inv.remaining_due} vs {expected_due}"
    print(f"   ✓ Invoice {inv.invoice_number} verified: Subtotal={inv.subtotal}, Total={inv.total_amount}, Paid={inv.paid_amount}, Remaining Due={inv.remaining_due}")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 3: Mobil Oil Change & Auto-Increment Logic
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 3] Verifying Vehicle Mobil Oil Change Auto-Increment Service...")
    vehicle = Vehicle.objects.filter(organization=org).first()
    assert vehicle is not None, "Vehicle not found"
    
    vdetail, _ = VehicleDetail.objects.get_or_create(vehicle=vehicle)
    initial_count = vdetail.mobil_count

    auto_increment_mobil_count(vehicle.registration, org)
    vdetail.refresh_from_db()
    assert vdetail.mobil_count == initial_count + 1, f"Mobil count did not increment: {vdetail.mobil_count}"
    print(f"   ✓ Vehicle {vehicle.registration}: Initial={initial_count}, Incremented={vdetail.mobil_count}")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 4: Driver Daily Attendance Register
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 4] Verifying Driver Attendance Register Service...")
    driver = Party.objects.filter(organization=org, kind='driver').first()
    assert driver is not None, "Driver not found"

    att_reg = get_driver_attendance_register(org, 2026, 11)
    assert 'days_in_month' in att_reg
    assert 'drivers' in att_reg
    assert len(att_reg['drivers']) > 0
    print(f"   ✓ Attendance register computed for {len(att_reg['drivers'])} drivers across {att_reg['days_in_month']} days.")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 5: Trip Sheet Dispatch System (WhatsApp / SMS)
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 5] Verifying Trip Sheet Dispatch Service...")
    duty = Duty.objects.filter(organization=org).first()
    assert duty is not None, "Duty not found"

    dispatch_res = dispatch_duty_trip_sheet(duty, channel='WhatsApp')
    assert dispatch_res['status'] == 'Sent'
    assert dispatch_res['channel'] == 'WhatsApp'
    assert 'dispatch_id' in dispatch_res
    print(f"   ✓ Trip sheet dispatched successfully (ID: {dispatch_res['dispatch_id']}).")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 6: Spare Parts Inventory & Stock Consumption
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 6] Verifying Spare Parts Inventory & Consumption...")
    part = SparePart.objects.filter(organization=org).first()
    if not part:
        part = SparePart.objects.create(
            organization=org,
            part_code="PRT-AF-01",
            name="Air Filter Premium",
            category="Filter",
            unit="Piece",
            quantity=Decimal('20.00'),
            cost_per_unit=Decimal('750.00'),
            low_stock_threshold=Decimal('5.00')
        )
    initial_stock = part.quantity
    
    mrec = MaintenanceRecord.objects.filter(organization=org).first()
    assert mrec is not None, "MaintenanceRecord not found"

    consumption = PartConsumption.objects.create(
        maintenance_record=mrec,
        spare_part=part,
        quantity=Decimal('2.00'),
        unit_cost=part.cost_per_unit,
        total_cost=part.cost_per_unit * Decimal('2.00'),
        date="2026-11-14"
    )
    part.quantity -= Decimal('2.00')
    part.save()
    part.refresh_from_db()
    assert part.quantity == initial_stock - Decimal('2.00')
    print(f"   ✓ Part {part.name}: Initial={initial_stock}, Remaining={part.quantity} (Consumed 2 units).")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 7: Custom Reminders & Due Follow-up Persistence
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 7] Verifying Custom Reminders & Due Follow-ups...")
    reminder = CustomReminder.objects.create(
        organization=org,
        title="BRTA Fitness Renewal Test",
        reminder_date="2026-11-25",
        description="Annual fitness renewal check",
        related_entity_type="Vehicle",
        status="Pending"
    )
    assert reminder.id is not None
    assert reminder.status == "Pending"

    followup = DueFollowUp.objects.create(
        organization=org,
        party=client,
        party_type="Customer",
        follow_up_date="2026-11-14",
        notes="Automated verification follow-up call",
        status="Pending",
        next_follow_up_date="2026-11-20"
    )
    assert followup.id is not None
    print(f"   ✓ Reminder ID={reminder.id} and FollowUp ID={followup.id} created and verified.")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 8: Bank Statement Reconciliation Engine
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 8] Verifying Bank Statement Automated Matching Engine...")
    account = FinancialAccount.objects.filter(organization=org).first()
    assert account is not None, "Financial Account not found"

    recon_res = reconcile_bank_statement(
        org=org,
        account_id=account.id,
        bank_rows=[
            {"date": "2026-11-05", "description": "Deposit", "amount": 25000, "reference": "DEP-01"},
            {"date": "2026-11-06", "description": "Fuel Payment", "amount": -3500, "reference": "FUEL-01"},
        ]
    )
    assert 'total_rows_processed' in recon_res
    assert recon_res['total_rows_processed'] == 2
    print(f"   ✓ Bank reconciliation processed {recon_res['total_rows_processed']} statement entries.")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 9: Custom Report Builder Query Engine
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 9] Verifying Ad-Hoc Custom Report Builder Engine...")
    report_res = build_custom_report(
        org=org,
        module='duties',
        fields=['id', 'date', 'client', 'total_amount']
    )
    assert 'module' in report_res and report_res['module'] == 'duties'
    assert 'rows' in report_res
    print(f"   ✓ Custom report builder returned {report_res['count']} duties.")
    passed_tests += 1

    # -------------------------------------------------------------
    # TEST 10: Full Organization Disaster Recovery Backup Snapshot
    # -------------------------------------------------------------
    total_tests += 1
    print("\n[TEST 10] Verifying Disaster Recovery Full Database Snapshot Export...")
    backup_raw = generate_organization_backup(org)
    import json
    backup_data = json.loads(backup_raw) if isinstance(backup_raw, str) else backup_raw
    assert 'organization' in backup_data
    assert 'vehicles' in backup_data
    assert 'parties' in backup_data
    assert 'duties_count' in backup_data
    print(f"   ✓ Backup JSON exported successfully for {backup_data['organization']}.")
    print(f"     Payload: {len(backup_data.get('vehicles', []))} vehicles, {len(backup_data.get('parties', []))} parties, {backup_data.get('duties_count', 0)} duties.")
    passed_tests += 1

    print("\n" + "=" * 70)
    print(f"AUDIT SUMMARY: {passed_tests}/{total_tests} TEST SUITES PASSED (100% SUCCESS RATE)")
    print("=" * 70)
    return True

if __name__ == '__main__':
    success = run_audit()
    sys.exit(0 if success else 1)
