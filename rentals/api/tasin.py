from decimal import Decimal
from datetime import date, datetime
from typing import Literal, Optional
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum, Q
from django.http import HttpRequest
from ninja import Router, Schema
from .errors import ApiError
from ..models import (
    Organization, Membership, Party, Vehicle,
    VehicleDetail, PartyDetail, Duty, Garage, MaintenanceRecord,
    VehiclePaper, FinancialAccount, AccountTransaction, SystemAlert, CompanyProfile,
    TasinInvoice, TasinInvoiceItem, TasinInvoicePayment,
    DriverAttendance, SparePart, PartConsumption, DueFollowUp,
    CustomReminder, TripDispatch, PrivateDocument, Agreement
)
from ..services.tasin import (
    get_dashboard_kpis, get_or_create_company_profile,
    generate_invoice_number, calculate_vehicle_profitability,
    get_document_expiries_report, get_account_ledger_report,
    calculate_previous_month_comparison, auto_increment_mobil_count,
    get_driver_attendance_register, dispatch_duty_trip_sheet,
    check_duplicate_expense, renew_agreement_service,
    reconcile_bank_statement, build_custom_report,
    generate_organization_backup
)
from ..rbac import get_user_permissions_payload, get_user_role, check_permission

router = Router()
D = Decimal

def _get_org(request: HttpRequest, org_id: int | None = None) -> Organization:
    if org_id:
        org = Organization.objects.filter(pk=org_id).first()
        if org:
            return org
    # Fallback to user's first org or first organization in database
    if getattr(request, 'user', None) and request.user.is_authenticated:
        m = Membership.objects.filter(user=request.user).first()
        if m:
            return m.organization
    org = Organization.objects.first()
    if not org:
        org = Organization.objects.create(name='TASIN ENTERPRISE')
    return org

# ─── SCHEMAS ─────────────────────────────────────────────────────────────────

class DutyIn(Schema):
    date: date
    client_id: Optional[str] = ''
    client_name: str
    vehicle_id: Optional[str] = ''
    vehicle_reg: str
    driver_id: Optional[str] = ''
    driver_name: str
    pickup_location: str
    destination: str
    start_time: str
    end_time: str = ''
    duty_type: Literal['Monthly', 'Daily', 'Trip', 'Outstation'] = 'Daily'
    customer_contact: str = ''
    description: str = ''
    agreed_rent: Decimal = D(0)
    fuel_cost: Decimal = D(0)
    overtime_charge: Decimal = D(0)
    driver_allowance: Decimal = D(0)
    additional_charge: Decimal = D(0)
    discount: Decimal = D(0)
    status: Literal['Scheduled', 'Running', 'Completed', 'Cancelled'] = 'Scheduled'

class DutyStatusIn(Schema):
    status: Literal['Scheduled', 'Running', 'Completed', 'Cancelled']

class OwnerIn(Schema):
    name: str
    phone: str
    alt_mobile: str = ''
    email: str = ''
    nid: str = ''
    present_address: str = ''
    permanent_address: str = ''
    bank_name: str = ''
    account_name: str = ''
    account_number: str = ''
    branch_name: str = ''
    account_type: str = 'Savings'
    joining_date: Optional[date] = None
    status: str = 'Active'

class VehicleInCustom(Schema):
    registration: Optional[str] = ''
    reg_no: Optional[str] = ''
    brand: str = 'Toyota'
    model: str = ''
    year: int = 2021
    category: str = 'Sedan'
    color: str = 'White'
    chassis_no: str = ''
    engine_no: str = ''
    registration_date: Optional[date] = None
    owner_id: Optional[int] = None
    driver_id: Optional[int] = None
    monthly_rent: Decimal = D(50000)
    status: Literal['Available', 'On Duty', 'In Garage', 'Inactive'] = 'Available'

class VehiclePaperIn(Schema):
    paper_type: Literal['registration', 'fitness', 'tax_token', 'insurance', 'route_permit', 'custom']
    paper_number: str = ''
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: str = 'Valid'
    document_url: str = ''

class DriverIn(Schema):
    name: str
    phone: str
    alt_mobile: str = ''
    nid: str = ''
    present_address: str = ''
    dob: Optional[date] = None
    joining_date: Optional[date] = None
    license_no: str = ''
    license_type: str = 'Professional'
    license_expiry: Optional[date] = None
    emergency_contact: str = ''
    assigned_vehicle_id: Optional[int] = None
    monthly_salary: Decimal = D(18000)
    daily_allowance: Decimal = D(300)
    advance: Decimal = D(0)
    status: str = 'Active'

class ClientIn(Schema):
    name: str
    contact_person: str
    phone: str
    email: str = ''
    address: str = ''
    billing_address: str = ''
    trade_license: str = ''
    client_type: Literal['Individual', 'Company', 'Government'] = 'Company'
    joining_date: Optional[date] = None
    status: str = 'Active'

class PaymentActionIn(Schema):
    amount: Decimal
    payment_method: str = 'Cash'
    reference: str = ''
    note: str = ''
    date: Optional[date] = None

class GarageIn(Schema):
    name: str
    contact_person: str = ''
    mobile: str
    address: str = ''
    garage_type: Literal['General', 'AC Specialist', 'Body Works', 'Engine', 'Electrical'] = 'General'
    status: str = 'Active'

class MaintenanceIn(Schema):
    vehicle_reg: str
    garage_name: str
    date: date
    problem_description: str
    service_type: Literal['Engine', 'Gearbox', 'Suspension', 'Brake', 'Electrical', 'AC', 'Tyre', 'Battery', 'Oil Change', 'General Service', 'Other'] = 'General Service'
    parts_details: str = ''
    labour_cost: Decimal = D(0)
    parts_cost: Decimal = D(0)
    expected_delivery: Optional[date] = None
    actual_delivery: Optional[date] = None
    status: Literal['Pending', 'In Progress', 'Completed', 'Delivered'] = 'Pending'

class ExpenseInCustom(Schema):
    date: date
    category: str
    title: str
    amount: Decimal
    payment_method: str = 'Cash'
    account_name: str = 'Main Cash Account'
    paid_to: str = ''
    reference: str = ''
    note: str = ''
    vehicle_reg: str = ''

class FinancialAccountIn(Schema):
    name: str
    account_number: str = ''
    account_type: Literal['Cash', 'Bank', 'Mobile Banking'] = 'Bank'
    bank_name: str = ''
    branch_name: str = ''
    opening_balance: Decimal = D(0)

class TransferIn(Schema):
    from_account_id: int
    to_account_id: int
    amount: Decimal
    reference: str = ''
    description: str = ''
    date: Optional[date] = None

class AlertIn(Schema):
    alert_type: Literal['payment', 'agreement', 'document', 'maintenance', 'custom'] = 'custom'
    severity: Literal['info', 'warning', 'danger'] = 'info'
    title: str
    detail: str = ''
    due_date: Optional[date] = None
    related_id: str = ''

class CompanyProfileIn(Schema):
    company_name: str
    address: str
    phone: str
    email: str
    website: str = ''
    bin: str = ''
    tin: str = ''
    details: str = ''
    currency: str = 'BDT'
    timezone: str = 'Asia/Dhaka'

class UserCreateIn(Schema):
    username: str
    password: str
    email: str = ''
    role: Literal['admin', 'accountant', 'operations', 'viewer', 'mechanic'] = 'operations'

class InvoiceItemIn(Schema):
    description: str
    quantity: Decimal = D(1)
    unit_price: Decimal
    duty_id: Optional[int] = None

class InvoiceCreateIn(Schema):
    client_id: int
    issue_date: date
    due_date: date
    discount: Decimal = D(0)
    tax: Decimal = D(0)
    notes: Optional[str] = ''
    duty_ids: list[int] = []
    custom_items: list[InvoiceItemIn] = []

class InvoicePaymentIn(Schema):
    date: date
    amount: Decimal
    payment_method: str = 'Cash'
    account_id: Optional[int] = None
    reference: Optional[str] = ''
    notes: Optional[str] = ''

class AttendanceIn(Schema):
    driver_id: int
    date: date
    status: Literal['Present', 'Absent', 'On Duty', 'Leave'] = 'Present'
    duty_id: Optional[int] = None
    notes: Optional[str] = ''

class DispatchIn(Schema):
    channel: Literal['SMS', 'WhatsApp'] = 'SMS'

class SparePartIn(Schema):
    part_code: str
    name: str
    category: str = 'Mobil/Oil'
    unit: str = 'Litre'
    quantity: Decimal = D(0)
    cost_per_unit: Decimal = D(0)
    supplier: Optional[str] = ''
    low_stock_threshold: Decimal = D(5)
    notes: Optional[str] = ''

class PartConsumeIn(Schema):
    maintenance_id: int
    quantity: Decimal = D(1)
    date: Optional[date] = None

class DueFollowUpIn(Schema):
    party_id: int
    party_type: str = 'Customer'
    follow_up_date: date
    contact_method: Literal['Phone Call', 'WhatsApp', 'In Person', 'Email'] = 'Phone Call'
    notes: str
    next_follow_up_date: Optional[date] = None
    responsible_staff: Optional[str] = ''
    status: Literal['Pending', 'Completed', 'Overdue'] = 'Pending'

class CustomReminderIn(Schema):
    title: str
    reminder_date: date
    reminder_time: str = '09:00 AM'
    description: Optional[str] = ''
    related_entity_type: str = 'General'
    related_entity_id: Optional[str] = ''
    assigned_staff: Optional[str] = ''
    status: Literal['Pending', 'Completed', 'Overdue'] = 'Pending'

class CustomReportIn(Schema):
    module: str
    fields: list[str] = []
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status_filter: Optional[str] = None

class BankReconRowIn(Schema):
    date: str
    amount: Decimal
    type: Literal['Credit', 'Debit'] = 'Credit'
    description: Optional[str] = ''
    reference: Optional[str] = ''

class BankReconIn(Schema):
    account_id: int
    rows: list[BankReconRowIn]

class DocumentUploadIn(Schema):
    entity_type: str
    entity_id: str
    document_type: str
    file_name: str
    file_base64: str
    mime_type: str = 'application/pdf'

class RenewAgreementIn(Schema):
    new_start_date: date
    new_end_date: date
    new_monthly_rent: Decimal

# ─── 0. SYSTEM HEALTH & CONNECTION CHECK (auth=None) ──────────────────────────

@router.get('/health', auth=None)
def health_check(request):
    """Universal healthcheck for server and proxy validation."""
    return {'status': 'ok', 'message': 'TASIN ENTERPRISE Backend is connected and running', 'version': '1.0.0', 'time': datetime.now().isoformat()}

@router.get('/connection/status', auth=None)
def connection_status(request):
    """UI Connection verification endpoint."""
    org = _get_org(request)
    profile = get_or_create_company_profile(org)
    return {
        'connected': True,
        'system': 'TASIN ENTERPRISE – Rent a Car Management Software',
        'organization_id': org.pk,
        'organization_name': org.name,
        'company_profile': {
            'name': profile.company_name,
            'phone': profile.phone,
            'email': profile.email,
            'address': profile.address,
            'currency': profile.currency,
            'timezone': profile.timezone
        },
        'total_modules': 15,
        'modules': [
            {'id': 1, 'name': 'Dashboard (ড্যাশবোর্ড)'},
            {'id': 2, 'name': 'Owners Management (গাড়ির মালিক ব্যবস্থাপনা)'},
            {'id': 3, 'name': 'Vehicle Management (গাড়ি ব্যবস্থাপনা)'},
            {'id': 4, 'name': 'Drivers Management (ড্রাইভার ব্যবস্থাপনা)'},
            {'id': 5, 'name': 'Clients & Companies Management (ক্লায়েন্ট ও কোম্পানি ব্যবস্থাপনা)'},
            {'id': 6, 'name': 'Daily Operations / Duty Management (দৈনিক ডিউটি ব্যবস্থাপনা)'},
            {'id': 7, 'name': 'Daily Expenses Management (দৈনিক খরচ ব্যবস্থাপনা)'},
            {'id': 8, 'name': 'Maintenance & Garage Management (গাড়ি মেরামত ও গ্যারেজ ব্যবস্থাপনা)'},
            {'id': 9, 'name': 'Agreements & Documents Management (চুক্তি ও কাগজপত্র ব্যবস্থাপনা)'},
            {'id': 10, 'name': 'Accounts & Transactions (হিসাব ও লেনদেন ব্যবস্থাপনা)'},
            {'id': 11, 'name': 'Due & Collection Management (বকেয়া ও টাকা আদায় ব্যবস্থাপনা)'},
            {'id': 12, 'name': 'Global Vehicle Search & Statement (গাড়ি সার্চ ও পূর্ণাঙ্গ হিসাব)'},
            {'id': 13, 'name': 'Reports & Analytics (রিপোর্ট ও বিশ্লেষণ)'},
            {'id': 14, 'name': 'Notifications & Alerts (নোটিফিকেশন ও সতর্কবার্তা)'},
            {'id': 15, 'name': 'System Settings & User Management (সিস্টেম সেটিংস ও ব্যবহারকারী ব্যবস্থাপনা)'},
        ]
    }

@router.get('/tasin/overview', auth=None)
def tasin_overview(request):
    """One-stop live snapshot feeding the entire frontend UI."""
    org = _get_org(request)
    kpis = get_dashboard_kpis(org)
    return {'org_id': org.pk, 'org_name': org.name, **kpis}

# ─── MODULE 01: DASHBOARD ────────────────────────────────────────────────────

@router.get('/orgs/{org_id}/dashboard/kpis', auth=None)
def dashboard_kpis(request, org_id: int, date_filter: str | None = None, period: str | None = None):
    org = _get_org(request, org_id)
    target = date.today()
    period_mode = period or 'Monthly'
    if date_filter:
        if date_filter in ('Daily', 'Weekly', 'Monthly', 'Yearly'):
            period_mode = date_filter
        else:
            try:
                target = date.fromisoformat(date_filter)
            except (ValueError, TypeError):
                target = date.today()
    kpis = get_dashboard_kpis(org, target, period=period_mode)
    kpis['prev_month_comparison'] = calculate_previous_month_comparison(org)
    return kpis

# ─── MODULE 02: OWNERS MANAGEMENT ────────────────────────────────────────────

@router.get('/orgs/{org_id}/tasin-owners', auth=None)
def list_owners(request, org_id: int):
    org = _get_org(request, org_id)
    owners = Party.objects.filter(organization=org, kind=Party.Kind.OWNER).select_related('detail').prefetch_related('vehicles')
    out = []
    for o in owners:
        det = getattr(o, 'detail', None)
        v_count = o.vehicles.count()
        total_rent = sum(getattr(v, 'detail', None) and v.detail.monthly_rent or 50000 for v in o.vehicles.all())
        payable = det.total_payable_owner if det else total_rent
        paid = det.total_paid_owner if det else D(0)
        out.append({
            'id': str(o.pk),
            'ownerId': f'OWN-{o.pk:03d}',
            'name': o.name,
            'mobile': o.phone,
            'altMobile': det.alt_mobile if det else '',
            'email': o.email,
            'nid': det.nid if det else (o.nid_number or ''),
            'presentAddress': det.present_address if det else '',
            'permanentAddress': det.permanent_address if det else '',
            'bankName': det.bank_name if det else '',
            'accountName': det.account_name if det else '',
            'accountNumber': det.account_number if det else '',
            'branchName': det.branch_name if det else '',
            'accountType': det.account_type if det else 'Savings',
            'joiningDate': det.joining_date.isoformat() if det and det.joining_date else '',
            'status': det.status if det else 'Active',
            'totalVehicles': v_count,
            'totalRent': float(total_rent),
            'totalPayable': float(payable),
            'totalPaid': float(paid),
            'due': float(payable - paid),
            'vehicles': [{
                'id': str(v.pk),
                'registration': v.registration,
                'model': v.model_name,
                'status': getattr(v, 'detail', None) and v.detail.status or 'Available'
            } for v in o.vehicles.all()]
        })
    return out

@router.post('/orgs/{org_id}/tasin-owners', auth=None)
def create_owner(request, org_id: int, payload: OwnerIn):
    org = _get_org(request, org_id)
    with transaction.atomic():
        p = Party.objects.create(
            organization=org,
            kind=Party.Kind.OWNER,
            name=payload.name.strip(),
            phone=payload.phone.strip(),
            email=payload.email.strip(),
            nid_number=payload.nid.strip()
        )
        PartyDetail.objects.create(
            party=p,
            alt_mobile=payload.alt_mobile.strip(),
            nid=payload.nid.strip(),
            present_address=payload.present_address.strip(),
            permanent_address=payload.permanent_address.strip(),
            bank_name=payload.bank_name.strip(),
            account_name=payload.account_name.strip(),
            account_number=payload.account_number.strip(),
            branch_name=payload.branch_name.strip(),
            account_type=payload.account_type.strip(),
            joining_date=payload.joining_date or date.today(),
            status=payload.status
        )
    return {'id': str(p.pk), 'ownerId': f'OWN-{p.pk:03d}', 'name': p.name, 'success': True}

@router.put('/orgs/{org_id}/tasin-owners/{owner_id}', auth=None)
def update_owner(request, org_id: int, owner_id: int, payload: OwnerIn):
    org = _get_org(request, org_id)
    p = Party.objects.filter(pk=owner_id, organization=org, kind=Party.Kind.OWNER).first()
    if not p: raise ApiError(404, 'Owner not found')
    p.name = payload.name.strip()
    p.phone = payload.phone.strip()
    p.email = payload.email.strip()
    p.nid_number = payload.nid.strip()
    p.save()
    det, _ = PartyDetail.objects.get_or_create(party=p)
    det.alt_mobile = payload.alt_mobile.strip()
    det.nid = payload.nid.strip()
    det.present_address = payload.present_address.strip()
    det.permanent_address = payload.permanent_address.strip()
    det.bank_name = payload.bank_name.strip()
    det.account_name = payload.account_name.strip()
    det.account_number = payload.account_number.strip()
    det.branch_name = payload.branch_name.strip()
    det.account_type = payload.account_type.strip()
    det.status = payload.status
    det.save()
    return {'id': str(p.pk), 'success': True}

@router.post('/orgs/{org_id}/tasin-owners/{owner_id}/payments', auth=None)
def pay_owner(request, org_id: int, owner_id: int, payload: PaymentActionIn):
    org = _get_org(request, org_id)
    p = Party.objects.filter(pk=owner_id, organization=org, kind=Party.Kind.OWNER).first()
    if not p: raise ApiError(404, 'Owner not found')
    det, _ = PartyDetail.objects.get_or_create(party=p)
    det.total_paid_owner += payload.amount
    det.save()
    # Record transaction
    AccountTransaction.objects.create(
        organization=org,
        transaction_id=f'TXN-{datetime.now().strftime("%Y%m%d%H%M%S")}',
        date=payload.date or date.today(),
        transaction_type='Expense',
        category='Owner Payment',
        related_person=p.name,
        amount=payload.amount,
        payment_method=payload.payment_method,
        reference=payload.reference,
        description=f'Payment to owner {p.name}: {payload.note}'
    )
    return {'success': True, 'owner': p.name, 'amountPaid': float(payload.amount), 'remainingDue': float(det.total_payable_owner - det.total_paid_owner)}

@router.delete('/orgs/{org_id}/tasin-owners/{owner_id}', auth=None)
def delete_owner(request, org_id: int, owner_id: int):
    org = _get_org(request, org_id)
    p = Party.objects.filter(pk=owner_id, organization=org, kind=Party.Kind.OWNER).first()
    if not p: raise ApiError(404, 'Owner not found')
    det, _ = PartyDetail.objects.get_or_create(party=p)
    det.status = 'Inactive'
    det.save()
    return {'id': str(p.pk), 'status': 'Inactive', 'success': True}

# ─── MODULE 03: VEHICLE MANAGEMENT ───────────────────────────────────────────

@router.get('/orgs/{org_id}/tasin-vehicles', auth=None)
def list_vehicles(request, org_id: int):
    org = _get_org(request, org_id)
    vehicles = Vehicle.objects.filter(organization=org).select_related('owner', 'detail', 'detail__assigned_driver')
    out = []
    for v in vehicles:
        det = getattr(v, 'detail', None)
        driver_name = ''
        if det and det.assigned_driver:
            driver_name = det.assigned_driver.name
        out.append({
            'id': str(v.pk),
            'vehicleId': f'VEH-{v.pk:03d}',
            'registration': v.registration,
            'regNo': v.registration,
            'brand': det.brand if det else 'Toyota',
            'model': det.model if det else v.model_name,
            'year': det.year if det else 2021,
            'category': det.category if det else 'Sedan',
            'color': det.color if det else 'White',
            'chassisNo': det.chassis_no if det else '',
            'engineNo': det.engine_no if det else '',
            'registrationDate': det.registration_date.isoformat() if det and det.registration_date else '',
            'status': det.status if det else 'Available',
            'ownerId': str(v.owner_id),
            'owner': v.owner.name,
            'driverId': str(det.assigned_driver_id) if det and det.assigned_driver_id else '',
            'driver': driver_name,
            'monthlyRent': float(det.monthly_rent if det else 50000),
            'mobilCount': det.mobil_count if det else 0,
            'lastMobilDate': det.last_mobil_date.isoformat() if det and det.last_mobil_date else '',
            'agreementStart': det.agreement_start.isoformat() if det and det.agreement_start else '',
            'agreementEnd': det.agreement_end.isoformat() if det and det.agreement_end else ''
        })
    return out

@router.post('/orgs/{org_id}/tasin-vehicles', auth=None)
def create_vehicle(request, org_id: int, payload: VehicleInCustom):
    org = _get_org(request, org_id)
    owner = Party.objects.filter(pk=payload.owner_id, organization=org).first() if payload.owner_id else None
    if not owner:
        owner = Party.objects.filter(organization=org, kind=Party.Kind.OWNER).first()
        if not owner:
            owner = Party.objects.create(organization=org, kind=Party.Kind.OWNER, name='Default Owner', phone='01700000000')
    driver = Party.objects.filter(pk=payload.driver_id, organization=org).first() if payload.driver_id else None

    reg = (payload.registration or payload.reg_no or '').strip()
    if not reg:
        reg = f"DHA-METRO-GA-{(Vehicle.objects.count() + 1):02d}-1001"
    with transaction.atomic():
        v = Vehicle.objects.create(
            organization=org,
            registration=reg,
            model_name=payload.model.strip() or 'Car',
            owner=owner
        )
        VehicleDetail.objects.create(
            vehicle=v,
            brand=payload.brand.strip(),
            model=payload.model.strip(),
            year=payload.year,
            category=payload.category,
            color=payload.color,
            chassis_no=payload.chassis_no,
            engine_no=payload.engine_no,
            registration_date=payload.registration_date or date.today(),
            status=payload.status,
            monthly_rent=payload.monthly_rent,
            agreement_start=date.today(),
            assigned_driver=driver
        )
    return {'id': str(v.pk), 'vehicleId': f'VEH-{v.pk:03d}', 'registration': v.registration, 'success': True}

@router.put('/orgs/{org_id}/tasin-vehicles/{vehicle_id}', auth=None)
def update_vehicle(request, org_id: int, vehicle_id: int, payload: VehicleInCustom):
    org = _get_org(request, org_id)
    v = Vehicle.objects.filter(pk=vehicle_id, organization=org).first()
    if not v: raise ApiError(404, 'Vehicle not found')
    v.registration = payload.registration.strip()
    v.model_name = payload.model.strip() or v.model_name
    if payload.owner_id:
        v.owner_id = payload.owner_id
    v.save()
    det, _ = VehicleDetail.objects.get_or_create(vehicle=v)
    det.brand = payload.brand.strip()
    det.model = payload.model.strip()
    det.year = payload.year
    det.category = payload.category
    det.color = payload.color
    det.status = payload.status
    det.monthly_rent = payload.monthly_rent
    if payload.driver_id:
        det.assigned_driver_id = payload.driver_id
    det.save()
    return {'id': str(v.pk), 'success': True}

@router.delete('/orgs/{org_id}/tasin-vehicles/{vehicle_id}', auth=None)
def delete_vehicle(request, org_id: int, vehicle_id: int):
    org = _get_org(request, org_id)
    v = Vehicle.objects.filter(pk=vehicle_id, organization=org).first()
    if not v: raise ApiError(404, 'Vehicle not found')
    det, _ = VehicleDetail.objects.get_or_create(vehicle=v)
    det.status = 'Inactive'
    det.save()
    return {'id': str(v.pk), 'status': 'Inactive', 'success': True}

@router.get('/orgs/{org_id}/tasin-vehicles/{vehicle_id}/papers', auth=None)
def get_vehicle_papers(request, org_id: int, vehicle_id: int):
    org = _get_org(request, org_id)
    papers = VehiclePaper.objects.filter(vehicle_id=vehicle_id, organization=org)
    return [{
        'id': p.pk,
        'paperType': p.paper_type,
        'paperNumber': p.paper_number,
        'issueDate': p.issue_date.isoformat() if p.issue_date else '',
        'expiryDate': p.expiry_date.isoformat() if p.expiry_date else '',
        'status': p.status,
        'documentUrl': p.document_url
    } for p in papers]

@router.post('/orgs/{org_id}/tasin-vehicles/{vehicle_id}/papers', auth=None)
def add_vehicle_paper(request, org_id: int, vehicle_id: int, payload: VehiclePaperIn):
    org = _get_org(request, org_id)
    v = Vehicle.objects.filter(pk=vehicle_id, organization=org).first()
    if not v: raise ApiError(404, 'Vehicle not found')
    p = VehiclePaper.objects.create(
        organization=org,
        vehicle=v,
        paper_type=payload.paper_type,
        paper_number=payload.paper_number,
        issue_date=payload.issue_date,
        expiry_date=payload.expiry_date,
        status=payload.status,
        document_url=payload.document_url
    )
    return {'id': p.pk, 'success': True}

# ─── MODULE 04: DRIVERS MANAGEMENT ───────────────────────────────────────────

@router.get('/orgs/{org_id}/tasin-drivers', auth=None)
def list_drivers(request, org_id: int):
    org = _get_org(request, org_id)
    drivers = Party.objects.filter(organization=org, kind=Party.Kind.DRIVER).select_related('detail', 'detail__assigned_vehicle')
    out = []
    for d in drivers:
        det = getattr(d, 'detail', None)
        v_reg = det.assigned_vehicle.registration if det and det.assigned_vehicle else ''
        salary = det.monthly_salary if det else D(18000)
        paid = det.total_paid_driver if det else D(0)
        adv = det.advance if det else D(0)
        due = salary - paid + adv
        out.append({
            'id': str(d.pk),
            'driverId': f'DRV-{d.pk:03d}',
            'name': d.name,
            'mobile': d.phone,
            'altMobile': det.alt_mobile if det else '',
            'nid': det.nid if det else (d.nid_number or ''),
            'presentAddress': det.present_address if det else '',
            'dob': det.dob.isoformat() if det and det.dob else '',
            'joiningDate': det.joining_date.isoformat() if det and det.joining_date else '',
            'licenseNo': det.license_no if det else (d.driving_licence_number or ''),
            'licenseType': det.license_type if det else 'Professional',
            'licenseExpiry': det.license_expiry.isoformat() if det and det.license_expiry else '',
            'emergencyContact': det.emergency_contact if det else '',
            'status': det.status if det else 'Active',
            'assignedVehicleId': str(det.assigned_vehicle_id) if det and det.assigned_vehicle_id else '',
            'vehicleReg': v_reg,
            'monthlySalary': float(salary),
            'dailyAllowance': float(det.daily_allowance if det else 300),
            'advance': float(adv),
            'totalPaid': float(paid),
            'remainingDue': float(due)
        })
    return out

@router.post('/orgs/{org_id}/tasin-drivers', auth=None)
def create_driver(request, org_id: int, payload: DriverIn):
    org = _get_org(request, org_id)
    veh = Vehicle.objects.filter(pk=payload.assigned_vehicle_id, organization=org).first() if payload.assigned_vehicle_id else None
    with transaction.atomic():
        d = Party.objects.create(
            organization=org,
            kind=Party.Kind.DRIVER,
            name=payload.name.strip(),
            phone=payload.phone.strip(),
            nid_number=payload.nid.strip(),
            driving_licence_number=payload.license_no.strip()
        )
        PartyDetail.objects.create(
            party=d,
            alt_mobile=payload.alt_mobile.strip(),
            nid=payload.nid.strip(),
            present_address=payload.present_address.strip(),
            dob=payload.dob,
            joining_date=payload.joining_date or date.today(),
            license_no=payload.license_no.strip(),
            license_type=payload.license_type,
            license_expiry=payload.license_expiry,
            emergency_contact=payload.emergency_contact.strip(),
            monthly_salary=payload.monthly_salary,
            daily_allowance=payload.daily_allowance,
            advance=payload.advance,
            status=payload.status,
            assigned_vehicle=veh
        )
    return {'id': str(d.pk), 'driverId': f'DRV-{d.pk:03d}', 'name': d.name, 'success': True}

@router.put('/orgs/{org_id}/tasin-drivers/{driver_id}', auth=None)
def update_driver(request, org_id: int, driver_id: int, payload: DriverIn):
    org = _get_org(request, org_id)
    d = Party.objects.filter(pk=driver_id, organization=org, kind=Party.Kind.DRIVER).first()
    if not d: raise ApiError(404, 'Driver not found')
    d.name = payload.name.strip()
    d.phone = payload.phone.strip()
    d.nid_number = payload.nid.strip()
    d.driving_licence_number = payload.license_no.strip()
    d.save()
    det, _ = PartyDetail.objects.get_or_create(party=d)
    det.alt_mobile = payload.alt_mobile.strip()
    det.present_address = payload.present_address.strip()
    det.license_type = payload.license_type
    det.license_expiry = payload.license_expiry
    det.emergency_contact = payload.emergency_contact.strip()
    det.monthly_salary = payload.monthly_salary
    det.daily_allowance = payload.daily_allowance
    det.advance = payload.advance
    det.status = payload.status
    if payload.assigned_vehicle_id:
        det.assigned_vehicle_id = payload.assigned_vehicle_id
    det.save()
    return {'id': str(d.pk), 'success': True}

@router.post('/orgs/{org_id}/tasin-drivers/{driver_id}/payments', auth=None)
def pay_driver(request, org_id: int, driver_id: int, payload: PaymentActionIn):
    org = _get_org(request, org_id)
    d = Party.objects.filter(pk=driver_id, organization=org, kind=Party.Kind.DRIVER).first()
    if not d: raise ApiError(404, 'Driver not found')
    det, _ = PartyDetail.objects.get_or_create(party=d)
    det.total_paid_driver += payload.amount
    det.save()
    AccountTransaction.objects.create(
        organization=org,
        transaction_id=f'TXN-{datetime.now().strftime("%Y%m%d%H%M%S")}',
        date=payload.date or date.today(),
        transaction_type='Expense',
        category='Driver Salary',
        related_person=d.name,
        amount=payload.amount,
        payment_method=payload.payment_method,
        reference=payload.reference,
        description=f'Salary / Payout to driver {d.name}: {payload.note}'
    )
    return {'success': True, 'driver': d.name, 'amountPaid': float(payload.amount), 'remainingDue': float(det.monthly_salary - det.total_paid_driver + det.advance)}

@router.delete('/orgs/{org_id}/tasin-drivers/{driver_id}', auth=None)
def delete_driver(request, org_id: int, driver_id: int):
    org = _get_org(request, org_id)
    d = Party.objects.filter(pk=driver_id, organization=org, kind=Party.Kind.DRIVER).first()
    if not d: raise ApiError(404, 'Driver not found')
    det, _ = PartyDetail.objects.get_or_create(party=d)
    det.status = 'Inactive'
    det.save()
    return {'id': str(d.pk), 'status': 'Inactive', 'success': True}

# ─── MODULE 05: CLIENTS & COMPANIES MANAGEMENT ───────────────────────────────

@router.get('/orgs/{org_id}/tasin-clients', auth=None)
def list_clients(request, org_id: int):
    org = _get_org(request, org_id)
    clients = Party.objects.filter(organization=org, kind=Party.Kind.CUSTOMER).select_related('detail')
    out = []
    for c in clients:
        det = getattr(c, 'detail', None)
        rec = det.total_receivable_client if det else D(0)
        col = det.total_collected_client if det else D(0)
        out.append({
            'id': str(c.pk),
            'clientId': f'CLT-{c.pk:03d}',
            'name': c.name,
            'contactPerson': det.contact_person if det else '',
            'mobile': c.phone,
            'email': c.email,
            'address': det.present_address if det else '',
            'billingAddress': det.billing_address if det else '',
            'tradeLicense': det.trade_license if det else '',
            'clientType': det.client_type if det else 'Company',
            'joiningDate': det.joining_date.isoformat() if det and det.joining_date else '',
            'status': det.status if det else 'Active',
            'totalReceivable': float(rec),
            'totalCollected': float(col),
            'due': float(max(D(0), rec - col))
        })
    return out

@router.get('/orgs/{org_id}/tasin-clients/{client_id}/profile', auth=None)
def get_client_profile(request, org_id: int, client_id: int):
    org = _get_org(request, org_id)
    c = Party.objects.filter(pk=client_id, organization=org, kind=Party.Kind.CUSTOMER).first()
    if not c: raise ApiError(404, 'Client not found')
    det = getattr(c, 'detail', None)

    # Associated duties
    duties = Duty.objects.filter(organization=org, client=c).order_by('-date')
    duties_data = [{
        'id': str(d.pk),
        'dutyId': d.duty_id,
        'date': d.date.isoformat(),
        'vehicleReg': d.vehicle_reg,
        'driverName': d.driver_name,
        'pickup': d.pickup_location,
        'destination': d.destination,
        'dutyType': d.duty_type,
        'totalAmount': float(d.total_amount),
        'status': d.status,
        'isInvoiced': d.is_invoiced,
        'invoiceNumber': d.invoice.invoice_number if d.invoice else ''
    } for d in duties]

    # Associated invoices
    invoices = TasinInvoice.objects.filter(organization=org, client=c).order_by('-issue_date')
    invoices_data = [{
        'id': str(inv.pk),
        'invoiceNumber': inv.invoice_number,
        'issueDate': inv.issue_date.isoformat(),
        'dueDate': inv.due_date.isoformat(),
        'subtotal': float(inv.subtotal),
        'discount': float(inv.discount),
        'tax': float(inv.tax),
        'totalAmount': float(inv.total_amount),
        'paidAmount': float(inv.paid_amount),
        'remainingDue': float(inv.remaining_due),
        'status': inv.status
    } for inv in invoices]

    # Payment history
    payments = AccountTransaction.objects.filter(
        organization=org,
        related_person=c.name,
        transaction_type='Income'
    ).order_by('-date')
    payments_data = [{
        'id': str(p.pk),
        'transactionId': p.transaction_id,
        'date': p.date.isoformat(),
        'amount': float(p.amount),
        'method': p.payment_method,
        'reference': p.reference,
        'description': p.description
    } for p in payments]

    rec = det.total_receivable_client if det else D(0)
    col = det.total_collected_client if det else D(0)

    return {
        'id': str(c.pk),
        'clientId': f'CLT-{c.pk:03d}',
        'name': c.name,
        'contactPerson': det.contact_person if det else '',
        'mobile': c.phone,
        'email': c.email,
        'address': det.present_address if det else '',
        'billingAddress': det.billing_address if det else '',
        'tradeLicense': det.trade_license if det else '',
        'clientType': det.client_type if det else 'Company',
        'joiningDate': det.joining_date.isoformat() if det and det.joining_date else '',
        'status': det.status if det else 'Active',
        'totalReceivable': float(rec),
        'totalCollected': float(col),
        'due': float(max(D(0), rec - col)),
        'duties': duties_data,
        'invoices': invoices_data,
        'payments': payments_data
    }

@router.post('/orgs/{org_id}/tasin-clients', auth=None)
def create_client(request, org_id: int, payload: ClientIn):
    org = _get_org(request, org_id)
    with transaction.atomic():
        c = Party.objects.create(
            organization=org,
            kind=Party.Kind.CUSTOMER,
            name=payload.name.strip(),
            phone=payload.phone.strip(),
            email=payload.email.strip()
        )
        PartyDetail.objects.create(
            party=c,
            contact_person=payload.contact_person.strip(),
            present_address=payload.address.strip(),
            billing_address=payload.billing_address.strip() or payload.address.strip(),
            trade_license=payload.trade_license.strip(),
            client_type=payload.client_type,
            joining_date=payload.joining_date or date.today(),
            status=payload.status
        )
    return {'id': str(c.pk), 'clientId': f'CLT-{c.pk:03d}', 'name': c.name, 'success': True}

@router.post('/orgs/{org_id}/tasin-clients/{client_id}/collections', auth=None)
def collect_client_payment(request, org_id: int, client_id: int, payload: PaymentActionIn):
    org = _get_org(request, org_id)
    c = Party.objects.filter(pk=client_id, organization=org, kind=Party.Kind.CUSTOMER).first()
    if not c: raise ApiError(404, 'Client not found')
    det, _ = PartyDetail.objects.get_or_create(party=c)
    det.total_collected_client += payload.amount
    det.save()
    AccountTransaction.objects.create(
        organization=org,
        transaction_id=f'TXN-{datetime.now().strftime("%Y%m%d%H%M%S")}',
        date=payload.date or date.today(),
        transaction_type='Income',
        category='Customer Collection',
        related_person=c.name,
        amount=payload.amount,
        payment_method=payload.payment_method,
        reference=payload.reference,
        description=f'Payment received from client {c.name}: {payload.note}'
    )
    return {'success': True, 'client': c.name, 'amountCollected': float(payload.amount), 'remainingDue': float(det.total_receivable_client - det.total_collected_client)}

@router.put('/orgs/{org_id}/tasin-clients/{client_id}', auth=None)
def update_client(request, org_id: int, client_id: int, payload: ClientIn):
    org = _get_org(request, org_id)
    c = Party.objects.filter(pk=client_id, organization=org, kind=Party.Kind.CUSTOMER).first()
    if not c: raise ApiError(404, 'Client not found')
    c.name = payload.name.strip()
    c.phone = payload.phone.strip()
    c.email = payload.email.strip()
    c.save()
    det, _ = PartyDetail.objects.get_or_create(party=c)
    det.contact_person = payload.contact_person.strip()
    det.present_address = payload.address.strip()
    det.billing_address = payload.billing_address.strip() or payload.address.strip()
    det.trade_license = payload.trade_license.strip()
    det.client_type = payload.client_type
    det.status = payload.status
    det.save()
    return {'id': str(c.pk), 'clientId': f'CLT-{c.pk:03d}', 'name': c.name, 'success': True}

@router.delete('/orgs/{org_id}/tasin-clients/{client_id}', auth=None)
def delete_client(request, org_id: int, client_id: int):
    org = _get_org(request, org_id)
    c = Party.objects.filter(pk=client_id, organization=org, kind=Party.Kind.CUSTOMER).first()
    if not c: raise ApiError(404, 'Client not found')
    c.delete()
    return {'success': True, 'id': str(client_id)}

# ─── INVOICE ENGINE ENDPOINTS (Module 05 & 06) ────────────────────────────────

@router.get('/orgs/{org_id}/tasin-invoices', auth=None)
def list_invoices(request, org_id: int, client_id: Optional[int] = None, status: Optional[str] = None):
    org = _get_org(request, org_id)
    qs = TasinInvoice.objects.filter(organization=org).select_related('client').prefetch_related('items', 'payments')
    if client_id:
        qs = qs.filter(client_id=client_id)
    if status and status != 'All':
        qs = qs.filter(status=status)

    return [{
        'id': str(inv.pk),
        'invoiceNumber': inv.invoice_number,
        'clientId': str(inv.client_id),
        'clientName': inv.client_name,
        'issueDate': inv.issue_date.isoformat(),
        'dueDate': inv.due_date.isoformat(),
        'subtotal': float(inv.subtotal),
        'discount': float(inv.discount),
        'tax': float(inv.tax),
        'totalAmount': float(inv.total_amount),
        'paidAmount': float(inv.paid_amount),
        'remainingDue': float(inv.remaining_due),
        'status': inv.status,
        'notes': inv.notes,
        'itemsCount': inv.items.count(),
        'items': [{
            'id': str(it.pk),
            'description': it.description,
            'quantity': float(it.quantity),
            'unitPrice': float(it.unit_price),
            'totalPrice': float(it.total_price),
            'dutyId': str(it.duty_id) if it.duty_id else ''
        } for it in inv.items.all()]
    } for inv in qs]

@router.post('/orgs/{org_id}/tasin-invoices', auth=None)
def create_invoice(request, org_id: int, payload: InvoiceCreateIn):
    org = _get_org(request, org_id)
    client = Party.objects.filter(pk=payload.client_id, organization=org, kind=Party.Kind.CUSTOMER).first()
    if not client: raise ApiError(404, 'Client not found')

    with transaction.atomic():
        invoice_num = generate_invoice_number(org)
        inv = TasinInvoice.objects.create(
            organization=org,
            invoice_number=invoice_num,
            client=client,
            client_name=client.name,
            issue_date=payload.issue_date,
            due_date=payload.due_date,
            discount=payload.discount,
            tax=payload.tax,
            notes=payload.notes or '',
            status='Sent'
        )

        subtotal = D(0)
        # 1. Attach duties
        if payload.duty_ids:
            duties = Duty.objects.filter(pk__in=payload.duty_ids, organization=org, is_invoiced=False)
            for d in duties:
                item_total = d.total_amount
                subtotal += item_total
                TasinInvoiceItem.objects.create(
                    invoice=inv,
                    duty=d,
                    description=f"Trip on {d.date}: {d.vehicle_reg} ({d.pickup_location} → {d.destination})",
                    quantity=D(1),
                    unit_price=item_total,
                    total_price=item_total
                )
                d.is_invoiced = True
                d.invoice = inv
                d.save()

        # 2. Attach custom items
        if payload.custom_items:
            for ci in payload.custom_items:
                tot = ci.quantity * ci.unit_price
                subtotal += tot
                TasinInvoiceItem.objects.create(
                    invoice=inv,
                    description=ci.description,
                    quantity=ci.quantity,
                    unit_price=ci.unit_price,
                    total_price=tot
                )

        inv.subtotal = subtotal
        inv.total_amount = max(D(0), subtotal - payload.discount + payload.tax)
        inv.save()

        # Update client receivable
        det, _ = PartyDetail.objects.get_or_create(party=client)
        det.total_receivable_client += inv.total_amount
        det.save()

    return {
        'id': str(inv.pk),
        'invoiceNumber': inv.invoice_number,
        'totalAmount': float(inv.total_amount),
        'status': inv.status,
        'success': True
    }

@router.get('/orgs/{org_id}/tasin-invoices/{invoice_id}', auth=None)
def get_invoice(request, org_id: int, invoice_id: int):
    org = _get_org(request, org_id)
    inv = TasinInvoice.objects.filter(pk=invoice_id, organization=org).select_related('client').prefetch_related('items', 'payments').first()
    if not inv: raise ApiError(404, 'Invoice not found')

    return {
        'id': str(inv.pk),
        'invoiceNumber': inv.invoice_number,
        'clientId': str(inv.client_id),
        'clientName': inv.client_name,
        'issueDate': inv.issue_date.isoformat(),
        'dueDate': inv.due_date.isoformat(),
        'subtotal': float(inv.subtotal),
        'discount': float(inv.discount),
        'tax': float(inv.tax),
        'totalAmount': float(inv.total_amount),
        'paidAmount': float(inv.paid_amount),
        'remainingDue': float(inv.remaining_due),
        'status': inv.status,
        'notes': inv.notes,
        'items': [{
            'id': str(it.pk),
            'description': it.description,
            'quantity': float(it.quantity),
            'unitPrice': float(it.unit_price),
            'totalPrice': float(it.total_price),
            'dutyId': str(it.duty_id) if it.duty_id else ''
        } for it in inv.items.all()],
        'payments': [{
            'id': str(p.pk),
            'paymentId': p.payment_id,
            'date': p.date.isoformat(),
            'amount': float(p.amount),
            'method': p.payment_method,
            'reference': p.reference,
            'notes': p.notes
        } for p in inv.payments.all()]
    }

@router.post('/orgs/{org_id}/tasin-invoices/{invoice_id}/payments', auth=None)
def record_invoice_payment(request, org_id: int, invoice_id: int, payload: InvoicePaymentIn):
    org = _get_org(request, org_id)
    with transaction.atomic():
        inv = TasinInvoice.objects.select_for_update().filter(pk=invoice_id, organization=org).first()
        if not inv: raise ApiError(404, 'Invoice not found')

        rem = inv.total_amount - inv.paid_amount
        if payload.amount <= 0:
            raise ApiError(422, 'Payment amount must be greater than zero')
        if payload.amount > rem:
            raise ApiError(422, f'Payment amount (৳{payload.amount}) exceeds remaining due (৳{rem})')

        p_count = inv.payments.count() + 1
        pay_id = f"PAY-{inv.invoice_number}-{p_count:02d}"

        account = None
        if payload.account_id:
            account = FinancialAccount.objects.select_for_update().filter(pk=payload.account_id, organization=org).first()
        if not account:
            account = FinancialAccount.objects.select_for_update().filter(organization=org, is_active=True).first()

        if account:
            account.current_balance += payload.amount
            account.save()

        p = TasinInvoicePayment.objects.create(
            invoice=inv,
            payment_id=pay_id,
            date=payload.date,
            amount=payload.amount,
            payment_method=payload.payment_method,
            account=account,
            reference=payload.reference or '',
            notes=payload.notes or ''
        )

        inv.paid_amount += payload.amount
        if inv.paid_amount >= inv.total_amount:
            inv.status = 'Paid'
        else:
            inv.status = 'Partially Paid'
        inv.save()

        # Update client collections
        det, _ = PartyDetail.objects.get_or_create(party=inv.client)
        det.total_collected_client += payload.amount
        det.save()

        # Record transaction in accounts ledger
        AccountTransaction.objects.create(
            organization=org,
            transaction_id=f"TXN-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            date=payload.date,
            transaction_type='Income',
            category='Customer Collection',
            related_person=inv.client_name,
            amount=payload.amount,
            payment_method=payload.payment_method,
            reference=payload.reference or inv.invoice_number,
            description=f"Invoice Payment for {inv.invoice_number}: {payload.notes or 'Settlement'}",
            to_account=account
        )

    return {
        'success': True,
        'invoiceNumber': inv.invoice_number,
        'amountPaid': float(payload.amount),
        'paidAmount': float(inv.paid_amount),
        'remainingDue': float(inv.remaining_due),
        'status': inv.status
    }

# ─── MODULE 06: DAILY OPERATIONS / DUTY MANAGEMENT ───────────────────────────

@router.get('/orgs/{org_id}/tasin-duties', auth=None)
def list_duties(request, org_id: int, date_val: str | None = None, status: str | None = None):
    org = _get_org(request, org_id)
    qs = Duty.objects.filter(organization=org)
    if date_val:
        qs = qs.filter(date=date_val)
    if status and status != 'All':
        qs = qs.filter(status=status)
    out = []
    for d in qs:
        out.append({
            'id': str(d.pk),
            'dutyId': d.duty_id,
            'date': d.date.isoformat(),
            'clientId': str(d.client_id) if d.client_id else '',
            'clientName': d.client_name,
            'vehicleId': str(d.vehicle_id) if d.vehicle_id else '',
            'vehicleReg': d.vehicle_reg,
            'driverId': str(d.driver_id) if d.driver_id else '',
            'driverName': d.driver_name,
            'pickupLocation': d.pickup_location,
            'destination': d.destination,
            'startTime': d.start_time,
            'endTime': d.end_time,
            'dutyType': d.duty_type,
            'customerContact': d.customer_contact,
            'description': d.description,
            'agreedRent': float(d.agreed_rent),
            'fuelCost': float(d.fuel_cost),
            'overtimeCharge': float(d.overtime_charge),
            'driverAllowance': float(d.driver_allowance),
            'additionalCharge': float(d.additional_charge),
            'discount': float(d.discount),
            'totalAmount': float(d.total_amount),
            'status': d.status
        })
    return out

@router.post('/orgs/{org_id}/tasin-duties', auth=None)
def create_duty(request, org_id: int, payload: DutyIn):
    org = _get_org(request, org_id)
    total = payload.agreed_rent + payload.fuel_cost + payload.overtime_charge + payload.driver_allowance + payload.additional_charge - payload.discount
    count = Duty.objects.filter(organization=org).count() + 1
    duty_code = f'DTY-{count:03d}'
    client = Party.objects.filter(pk=int(payload.client_id), organization=org).first() if payload.client_id and payload.client_id.isdigit() else None
    vehicle = Vehicle.objects.filter(pk=int(payload.vehicle_id), organization=org).first() if payload.vehicle_id and payload.vehicle_id.isdigit() else None
    driver = Party.objects.filter(pk=int(payload.driver_id), organization=org).first() if payload.driver_id and payload.driver_id.isdigit() else None

    # Overlap and conflict validation
    if payload.status in ('Scheduled', 'Running'):
        if vehicle:
            conflict_v = Duty.objects.filter(
                organization=org,
                vehicle=vehicle,
                date=payload.date,
                status__in=['Scheduled', 'Running']
            ).first()
            if conflict_v:
                raise ApiError(409, f"Vehicle {payload.vehicle_reg} is already booked for Duty {conflict_v.duty_id} on {payload.date}")
        if driver:
            conflict_d = Duty.objects.filter(
                organization=org,
                driver=driver,
                date=payload.date,
                status__in=['Scheduled', 'Running']
            ).first()
            if conflict_d:
                raise ApiError(409, f"Driver {payload.driver_name} is already assigned to Duty {conflict_d.duty_id} on {payload.date}")

    d = Duty.objects.create(
        organization=org,
        duty_id=duty_code,
        date=payload.date,
        client=client,
        client_name=payload.client_name.strip(),
        vehicle=vehicle,
        vehicle_reg=payload.vehicle_reg.strip(),
        driver=driver,
        driver_name=payload.driver_name.strip(),
        pickup_location=payload.pickup_location.strip(),
        destination=payload.destination.strip(),
        start_time=payload.start_time.strip(),
        end_time=payload.end_time.strip(),
        duty_type=payload.duty_type,
        customer_contact=payload.customer_contact.strip(),
        description=payload.description.strip(),
        agreed_rent=payload.agreed_rent,
        fuel_cost=payload.fuel_cost,
        overtime_charge=payload.overtime_charge,
        driver_allowance=payload.driver_allowance,
        additional_charge=payload.additional_charge,
        discount=payload.discount,
        total_amount=total,
        status=payload.status
    )
    return {'id': str(d.pk), 'dutyId': d.duty_id, 'totalAmount': float(d.total_amount), 'success': True}

@router.post('/orgs/{org_id}/tasin-duties/{duty_id}/status', auth=None)
def set_duty_status(request, org_id: int, duty_id: int, payload: DutyStatusIn):
    org = _get_org(request, org_id)
    d = Duty.objects.filter(pk=duty_id, organization=org).first()
    if not d: raise ApiError(404, 'Duty not found')
    d.status = payload.status
    d.save()
    # Update vehicle status if Running or Completed
    if d.vehicle:
        det, _ = VehicleDetail.objects.get_or_create(vehicle=d.vehicle)
        if payload.status == 'Running':
            det.status = 'On Duty'
        elif payload.status == 'Completed':
            det.status = 'Available'
        det.save()
    return {'id': str(d.pk), 'status': d.status, 'success': True}

# ─── MODULE 07: DAILY EXPENSES MANAGEMENT ─────────────────────────────────────

@router.get('/orgs/{org_id}/tasin-expenses', auth=None)
def list_daily_expenses(request, org_id: int, date_val: str | None = None, category: str | None = None):
    org = _get_org(request, org_id)
    qs = AccountTransaction.objects.filter(organization=org, transaction_type='Expense')
    if date_val:
        qs = qs.filter(date=date_val)
    if category and category != 'All':
        qs = qs.filter(category=category)
    return [{
        'id': str(t.pk),
        'expenseId': t.transaction_id,
        'date': t.date.isoformat(),
        'category': t.category,
        'title': t.description or t.category,
        'amount': float(t.amount),
        'paymentMethod': t.payment_method,
        'paidTo': t.related_person,
        'reference': t.reference,
        'vehicleReg': t.vehicle_reg
    } for t in qs]

@router.post('/orgs/{org_id}/tasin-expenses', auth=None)
def record_daily_expense(request, org_id: int, payload: ExpenseInCustom):
    org = _get_org(request, org_id)
    if payload.reference and payload.reference.strip():
        dup = AccountTransaction.objects.filter(organization=org, reference=payload.reference.strip()).first()
        if dup:
            raise ApiError(409, f"Transaction with reference '{payload.reference}' already exists ({dup.transaction_id})")
    count = AccountTransaction.objects.filter(organization=org, transaction_type='Expense').count() + 1
    t = AccountTransaction.objects.create(
        organization=org,
        transaction_id=f'EXP-{count:03d}',
        date=payload.date,
        transaction_type='Expense',
        category=payload.category,
        related_person=payload.paid_to,
        vehicle_reg=payload.vehicle_reg,
        amount=payload.amount,
        payment_method=payload.payment_method,
        reference=payload.reference,
        description=f'{payload.title}: {payload.note}'
    )
    return {'id': str(t.pk), 'expenseId': t.transaction_id, 'amount': float(t.amount), 'success': True}

# ─── MODULE 08: MAINTENANCE & GARAGE MANAGEMENT ───────────────────────────────

@router.get('/orgs/{org_id}/tasin-garages', auth=None)
def list_garages(request, org_id: int):
    org = _get_org(request, org_id)
    garages = Garage.objects.filter(organization=org)
    return [{
        'id': str(g.pk),
        'garageId': g.garage_id,
        'name': g.name,
        'contactPerson': g.contact_person,
        'mobile': g.mobile,
        'address': g.address,
        'garageType': g.garage_type,
        'status': g.status,
        'totalBill': float(g.total_bill),
        'totalPaid': float(g.total_paid),
        'due': float(g.total_bill - g.total_paid)
    } for g in garages]

@router.post('/orgs/{org_id}/tasin-garages', auth=None)
def create_garage(request, org_id: int, payload: GarageIn):
    org = _get_org(request, org_id)
    count = Garage.objects.filter(organization=org).count() + 1
    g = Garage.objects.create(
        organization=org,
        garage_id=f'GRG-{count:03d}',
        name=payload.name.strip(),
        contact_person=payload.contact_person.strip(),
        mobile=payload.mobile.strip(),
        address=payload.address.strip(),
        garage_type=payload.garage_type,
        status=payload.status
    )
    return {'id': str(g.pk), 'garageId': g.garage_id, 'name': g.name, 'success': True}

@router.post('/orgs/{org_id}/tasin-garages/{garage_id}/payments', auth=None)
def record_garage_payment(request, org_id: int, garage_id: int, payload: PaymentActionIn):
    org = _get_org(request, org_id)
    g = Garage.objects.filter(pk=garage_id, organization=org).first()
    if not g: raise ApiError(404, 'Garage not found')
    with transaction.atomic():
        g.total_paid += payload.amount
        g.save()
        count = AccountTransaction.objects.filter(organization=org).count() + 1
        AccountTransaction.objects.create(
            organization=org,
            transaction_id=f'EXP-G-{count:03d}',
            date=payload.date or date.today(),
            transaction_type='Expense',
            category='Garage Bill',
            related_person=g.name,
            amount=payload.amount,
            payment_method=payload.payment_method,
            reference=payload.reference,
            description=f'Payment to garage {g.name}: {payload.note}'
        )
    return {'success': True, 'garage': g.name, 'amountPaid': float(payload.amount), 'remainingDue': float(g.total_bill - g.total_paid)}

@router.delete('/orgs/{org_id}/tasin-garages/{garage_id}', auth=None)
def delete_garage(request, org_id: int, garage_id: int):
    org = _get_org(request, org_id)
    g = Garage.objects.filter(pk=garage_id, organization=org).first()
    if not g: raise ApiError(404, 'Garage not found')
    g.status = 'Inactive'
    g.save()
    return {'id': str(g.pk), 'status': 'Inactive', 'success': True}

@router.get('/orgs/{org_id}/tasin-maintenance', auth=None)
def list_maintenance(request, org_id: int):
    org = _get_org(request, org_id)
    qs = MaintenanceRecord.objects.filter(organization=org)
    return [{
        'id': str(m.pk),
        'maintenanceId': m.maintenance_id,
        'vehicleReg': m.vehicle_reg,
        'garageName': m.garage_name,
        'date': m.date.isoformat(),
        'problemDescription': m.problem_description,
        'serviceType': m.service_type,
        'partsDetails': m.parts_details,
        'labourCost': float(m.labour_cost),
        'partsCost': float(m.parts_cost),
        'totalCost': float(m.total_cost),
        'expectedDelivery': m.expected_delivery.isoformat() if m.expected_delivery else '',
        'actualDelivery': m.actual_delivery.isoformat() if m.actual_delivery else '',
        'status': m.status
    } for m in qs]

@router.post('/orgs/{org_id}/tasin-maintenance', auth=None)
def create_maintenance(request, org_id: int, payload: MaintenanceIn):
    org = _get_org(request, org_id)
    total = payload.labour_cost + payload.parts_cost
    count = MaintenanceRecord.objects.filter(organization=org).count() + 1
    m = MaintenanceRecord.objects.create(
        organization=org,
        maintenance_id=f'MNT-{count:03d}',
        vehicle_reg=payload.vehicle_reg.strip(),
        garage_name=payload.garage_name.strip(),
        date=payload.date,
        problem_description=payload.problem_description.strip(),
        service_type=payload.service_type,
        parts_details=payload.parts_details.strip(),
        labour_cost=payload.labour_cost,
        parts_cost=payload.parts_cost,
        total_cost=total,
        expected_delivery=payload.expected_delivery,
        actual_delivery=payload.actual_delivery,
        status=payload.status
    )
    # Update garage bill
    g = Garage.objects.filter(organization=org, name=payload.garage_name.strip()).first()
    if g:
        g.total_bill += total
        g.save()
    # Update vehicle status and mobil count if applicable
    v = Vehicle.objects.filter(organization=org, registration=payload.vehicle_reg.strip()).first()
    if v:
        det, _ = VehicleDetail.objects.get_or_create(vehicle=v)
        if payload.status in ('Pending', 'In Progress'):
            det.status = 'In Garage'
        elif payload.status in ('Completed', 'Delivered'):
            det.status = 'Available'
        if payload.service_type == 'Oil Change':
            det.mobil_count += 1
            det.last_mobil_date = payload.date
        det.save()
    return {'id': str(m.pk), 'maintenanceId': m.maintenance_id, 'totalCost': float(m.total_cost), 'success': True}

@router.put('/orgs/{org_id}/tasin-maintenance/{mnt_id}', auth=None)
def update_maintenance(request, org_id: int, mnt_id: int, payload: MaintenanceIn):
    org = _get_org(request, org_id)
    m = MaintenanceRecord.objects.filter(pk=mnt_id, organization=org).first()
    if not m: raise ApiError(404, 'Maintenance record not found')
    old_total = m.total_cost
    new_total = payload.labour_cost + payload.parts_cost

    m.vehicle_reg = payload.vehicle_reg.strip()
    m.garage_name = payload.garage_name.strip()
    m.date = payload.date
    m.problem_description = payload.problem_description.strip()
    m.service_type = payload.service_type
    m.parts_details = payload.parts_details.strip()
    m.labour_cost = payload.labour_cost
    m.parts_cost = payload.parts_cost
    m.total_cost = new_total
    m.expected_delivery = payload.expected_delivery
    m.actual_delivery = payload.actual_delivery
    m.status = payload.status
    m.save()

    # Adjust garage bill
    g = Garage.objects.filter(organization=org, name=payload.garage_name.strip()).first()
    if g:
        g.total_bill += (new_total - old_total)
        g.save()

    # Update vehicle status and mobil count if applicable
    v = Vehicle.objects.filter(organization=org, registration=payload.vehicle_reg.strip()).first()
    if v:
        det, _ = VehicleDetail.objects.get_or_create(vehicle=v)
        if payload.status in ('Pending', 'In Progress'):
            det.status = 'In Garage'
        elif payload.status in ('Completed', 'Delivered'):
            det.status = 'Available'
        if payload.service_type == 'Oil Change':
            det.last_mobil_date = payload.date
        det.save()

    return {'id': str(m.pk), 'maintenanceId': m.maintenance_id, 'totalCost': float(m.total_cost), 'success': True}

@router.delete('/orgs/{org_id}/tasin-maintenance/{mnt_id}', auth=None)
def delete_maintenance(request, org_id: int, mnt_id: int):
    org = _get_org(request, org_id)
    m = MaintenanceRecord.objects.filter(pk=mnt_id, organization=org).first()
    if not m: raise ApiError(404, 'Maintenance record not found')
    g = Garage.objects.filter(organization=org, name=m.garage_name).first()
    if g:
        g.total_bill = max(D(0), g.total_bill - m.total_cost)
        g.save()
    v = Vehicle.objects.filter(organization=org, registration=m.vehicle_reg).first()
    if v:
        det = getattr(v, 'detail', None)
        if det and det.status == 'In Garage':
            det.status = 'Available'
            det.save()
    m.delete()
    return {'id': str(mnt_id), 'success': True}

# ─── MODULE 10: ACCOUNTS & TRANSACTIONS ───────────────────────────────────────

@router.get('/orgs/{org_id}/tasin-accounts', auth=None)
def list_accounts(request, org_id: int):
    org = _get_org(request, org_id)
    accounts = FinancialAccount.objects.filter(organization=org, is_active=True)
    return [{
        'id': str(a.pk),
        'accountId': a.account_id,
        'name': a.name,
        'accountNumber': a.account_number,
        'type': a.account_type,
        'bankName': a.bank_name,
        'openingBalance': float(a.opening_balance),
        'currentBalance': float(a.current_balance)
    } for a in accounts]

@router.post('/orgs/{org_id}/tasin-accounts', auth=None)
def create_account(request, org_id: int, payload: FinancialAccountIn):
    org = _get_org(request, org_id)
    count = FinancialAccount.objects.filter(organization=org).count() + 1
    a = FinancialAccount.objects.create(
        organization=org,
        account_id=f'ACC-{count:03d}',
        name=payload.name.strip(),
        account_number=payload.account_number.strip(),
        account_type=payload.account_type,
        bank_name=payload.bank_name.strip(),
        branch_name=payload.branch_name.strip(),
        opening_balance=payload.opening_balance,
        current_balance=payload.opening_balance
    )
    return {'id': str(a.pk), 'accountId': a.account_id, 'name': a.name, 'success': True}

@router.get('/orgs/{org_id}/tasin-transactions', auth=None)
def list_transactions(request, org_id: int):
    org = _get_org(request, org_id)
    txns = AccountTransaction.objects.filter(organization=org)[:50]
    return [{
        'id': str(t.pk),
        'transactionId': t.transaction_id,
        'date': t.date.isoformat(),
        'type': t.transaction_type,
        'category': t.category,
        'relatedPerson': t.related_person,
        'vehicleReg': t.vehicle_reg,
        'amount': float(t.amount),
        'paymentMethod': t.payment_method,
        'reference': t.reference,
        'description': t.description
    } for t in txns]

@router.post('/orgs/{org_id}/tasin-accounts/transfer', auth=None)
def transfer_money(request, org_id: int, payload: TransferIn):
    org = _get_org(request, org_id)
    from_acc = FinancialAccount.objects.filter(pk=payload.from_account_id, organization=org).first()
    to_acc = FinancialAccount.objects.filter(pk=payload.to_account_id, organization=org).first()
    if not from_acc or not to_acc: raise ApiError(404, 'Account not found')
    if from_acc.current_balance < payload.amount: raise ApiError(422, 'Insufficient funds')

    with transaction.atomic():
        from_acc.current_balance -= payload.amount
        to_acc.current_balance += payload.amount
        from_acc.save()
        to_acc.save()

        count = AccountTransaction.objects.filter(organization=org).count() + 1
        AccountTransaction.objects.create(
            organization=org,
            transaction_id=f'TXN-{count:03d}',
            date=payload.date or date.today(),
            transaction_type='Transfer',
            category='Money Transfer',
            amount=payload.amount,
            payment_method='Bank Transfer',
            reference=payload.reference,
            description=f'Transfer from {from_acc.name} to {to_acc.name}: {payload.description}',
            from_account=from_acc,
            to_account=to_acc
        )
    return {'success': True, 'from': from_acc.name, 'to': to_acc.name, 'amount': float(payload.amount)}

# ─── MODULE 11: DUE & COLLECTION MANAGEMENT ──────────────────────────────────

@router.get('/orgs/{org_id}/tasin-due-collection', auth=None)
def due_collection_summary(request, org_id: int):
    org = _get_org(request, org_id)
    # 1. Clients
    clients = Party.objects.filter(organization=org, kind=Party.Kind.CUSTOMER).select_related('detail')
    client_dues = []
    for c in clients:
        det = getattr(c, 'detail', None)
        rec = det.total_receivable_client if det else D(0)
        col = det.total_collected_client if det else D(0)
        rem = max(D(0), rec - col)
        client_dues.append({
            'id': str(c.pk),
            'name': c.name,
            'contactPerson': det.contact_person if det else '',
            'mobile': c.phone,
            'totalInvoice': float(rec),
            'paid': float(col),
            'remaining': float(rem),
            'overdueDays': 15 if rem > 0 else 0,
            'status': 'overdue' if rem > 0 else 'paid'
        })

    # 2. Owners
    owners = Party.objects.filter(organization=org, kind=Party.Kind.OWNER).select_related('detail')
    owner_dues = []
    for o in owners:
        det = getattr(o, 'detail', None)
        pay = det.total_payable_owner if det else D(0)
        paid = det.total_paid_owner if det else D(0)
        rem = max(D(0), pay - paid)
        owner_dues.append({
            'id': str(o.pk),
            'name': o.name,
            'mobile': o.phone,
            'vehicleCount': o.vehicles.count(),
            'totalPayable': float(pay),
            'paid': float(paid),
            'remaining': float(rem)
        })

    # 3. Drivers
    drivers = Party.objects.filter(organization=org, kind=Party.Kind.DRIVER).select_related('detail', 'detail__assigned_vehicle')
    driver_dues = []
    for d in drivers:
        det = getattr(d, 'detail', None)
        sal = det.monthly_salary if det else D(0)
        allow = det.daily_allowance if det else D(0)
        adv = det.advance if det else D(0)
        paid = det.total_paid_driver if det else D(0)
        rem = max(D(0), sal - paid + adv)
        driver_dues.append({
            'id': str(d.pk),
            'name': d.name,
            'mobile': d.phone,
            'vehicleReg': det.assigned_vehicle.registration if det and det.assigned_vehicle else '',
            'salary': float(sal),
            'allowance': float(allow),
            'advance': float(adv),
            'totalPaid': float(paid),
            'remaining': float(rem)
        })

    # 4. Garages
    garages = Garage.objects.filter(organization=org)
    garage_dues = []
    for g in garages:
        rem = max(D(0), g.total_bill - g.total_paid)
        garage_dues.append({
            'id': str(g.pk),
            'name': g.name,
            'contactPerson': g.contact_person,
            'mobile': g.mobile,
            'garageType': g.garage_type,
            'totalBill': float(g.total_bill),
            'paid': float(g.total_paid),
            'remaining': float(rem)
        })

    total_rec = sum(x['remaining'] for x in client_dues)
    total_own = sum(x['remaining'] for x in owner_dues)
    total_drv = sum(x['remaining'] for x in driver_dues)
    total_grg = sum(x['remaining'] for x in garage_dues)

    return {
        'clients': client_dues,
        'owners': owner_dues,
        'drivers': driver_dues,
        'garages': garage_dues,
        'totals': {
            'customerReceivable': total_rec,
            'ownerPayable': total_own,
            'driverPayable': total_drv,
            'garagePayable': total_grg,
            'totalDue': total_rec + total_own + total_drv + total_grg
        }
    }

# ─── MODULE 12: GLOBAL VEHICLE SEARCH & STATEMENT ─────────────────────────────

@router.get('/orgs/{org_id}/tasin-vehicle-search', auth=None)
def search_vehicles_global(request, org_id: int, q: str = ''):
    org = _get_org(request, org_id)
    qs = Vehicle.objects.filter(organization=org).select_related('owner', 'detail', 'detail__assigned_driver')
    if q:
        qs = qs.filter(
            Q(registration__icontains=q) |
            Q(model_name__icontains=q) |
            Q(owner__name__icontains=q) |
            Q(detail__assigned_driver__name__icontains=q)
        )
    return [{
        'id': str(v.pk),
        'registration': v.registration,
        'brand': getattr(v, 'detail', None) and v.detail.brand or 'Toyota',
        'model': v.model_name,
        'owner': v.owner.name,
        'driver': getattr(v, 'detail', None) and v.detail.assigned_driver and v.detail.assigned_driver.name or 'Unassigned',
        'status': getattr(v, 'detail', None) and v.detail.status or 'Available',
        'monthlyRent': float(getattr(v, 'detail', None) and v.detail.monthly_rent or 50000)
    } for v in qs]

@router.get('/orgs/{org_id}/tasin-vehicle-search/{vehicle_id}/statement', auth=None)
def vehicle_full_statement(request, org_id: int, vehicle_id: int):
    org = _get_org(request, org_id)
    v = Vehicle.objects.filter(pk=vehicle_id, organization=org).select_related('owner', 'detail', 'detail__assigned_driver').first()
    if not v: raise ApiError(404, 'Vehicle not found')
    det = getattr(v, 'detail', None)

    duties = Duty.objects.filter(vehicle=v)
    maintenances = MaintenanceRecord.objects.filter(vehicle=v)
    papers = VehiclePaper.objects.filter(vehicle=v)

    total_duty_income = sum(d.total_amount for d in duties.filter(status='Completed'))
    total_maintenance = sum(m.total_cost for m in maintenances)
    owner_rent = det.monthly_rent if det else D(50000)

    return {
        'vehicle': {
            'id': str(v.pk),
            'registration': v.registration,
            'brand': det.brand if det else 'Toyota',
            'model': v.model_name,
            'category': det.category if det else 'Sedan',
            'year': det.year if det else 2021,
            'color': det.color if det else 'White',
            'status': det.status if det else 'Available',
            'owner': v.owner.name,
            'driver': det.assigned_driver.name if det and det.assigned_driver else 'Unassigned'
        },
        'financials': {
            'totalIncome': float(total_duty_income),
            'ownerRent': float(owner_rent),
            'maintenanceCost': float(total_maintenance),
            'netProfit': float(total_duty_income - owner_rent - total_maintenance)
        },
        'papers': [{
            'type': p.paper_type,
            'number': p.paper_number,
            'expiry': p.expiry_date.isoformat() if p.expiry_date else '',
            'status': p.status
        } for p in papers],
        'dutyHistory': [{
            'id': d.duty_id,
            'date': d.date.isoformat(),
            'client': d.client_name,
            'driver': d.driver_name,
            'amount': float(d.total_amount),
            'status': d.status
        } for d in duties[:10]],
        'maintenanceHistory': [{
            'id': m.maintenance_id,
            'date': m.date.isoformat(),
            'garage': m.garage_name,
            'service': m.service_type,
            'cost': float(m.total_cost),
            'status': m.status
        } for m in maintenances[:10]]
    }

# ─── MODULE 14: NOTIFICATIONS & ALERTS ────────────────────────────────────────

@router.get('/orgs/{org_id}/tasin-alerts', auth=None)
def list_system_alerts(request, org_id: int):
    org = _get_org(request, org_id)
    alerts = SystemAlert.objects.filter(organization=org)
    return [{
        'id': str(a.pk),
        'alertId': a.alert_id,
        'type': a.alert_type,
        'severity': a.severity,
        'title': a.title,
        'detail': a.detail,
        'date': a.date.isoformat(),
        'dueDate': a.due_date.isoformat() if a.due_date else '',
        'relatedId': a.related_id,
        'isRead': a.is_read
    } for a in alerts]

@router.post('/orgs/{org_id}/tasin-alerts', auth=None)
def create_system_alert(request, org_id: int, payload: AlertIn):
    org = _get_org(request, org_id)
    count = SystemAlert.objects.filter(organization=org).count() + 1
    a = SystemAlert.objects.create(
        organization=org,
        alert_id=f'ALT-{count:03d}',
        alert_type=payload.alert_type,
        severity=payload.severity,
        title=payload.title.strip(),
        detail=payload.detail.strip(),
        date=date.today(),
        due_date=payload.due_date,
        related_id=payload.related_id
    )
    return {'id': str(a.pk), 'alertId': a.alert_id, 'success': True}

@router.post('/orgs/{org_id}/tasin-alerts/{alert_id}/read', auth=None)
def mark_alert_read(request, org_id: int, alert_id: int):
    org = _get_org(request, org_id)
    a = SystemAlert.objects.filter(pk=alert_id, organization=org).first()
    if not a: raise ApiError(404, 'Alert not found')
    a.is_read = True
    a.save()
    return {'id': str(a.pk), 'isRead': True, 'success': True}

# ─── MODULE 15: SETTINGS & USER MANAGEMENT ───────────────────────────────────

@router.get('/orgs/{org_id}/tasin-settings/company', auth=None)
def get_company_settings(request, org_id: int):
    org = _get_org(request, org_id)
    profile = get_or_create_company_profile(org)
    return {
        'companyName': profile.company_name,
        'address': profile.address,
        'phone': profile.phone,
        'email': profile.email,
        'website': profile.website,
        'bin': profile.bin,
        'tin': profile.tin,
        'details': profile.details,
        'currency': profile.currency,
        'timezone': profile.timezone
    }

@router.put('/orgs/{org_id}/tasin-settings/company', auth=None)
def update_company_settings(request, org_id: int, payload: CompanyProfileIn):
    org = _get_org(request, org_id)
    profile = get_or_create_company_profile(org)
    profile.company_name = payload.company_name.strip()
    profile.address = payload.address.strip()
    profile.phone = payload.phone.strip()
    profile.email = payload.email.strip()
    profile.website = payload.website.strip()
    profile.bin = payload.bin.strip()
    profile.tin = payload.tin.strip()
    profile.details = payload.details.strip()
    profile.currency = payload.currency.strip()
    profile.timezone = payload.timezone.strip()
    profile.save()
    return {'success': True, 'companyName': profile.company_name}

@router.get('/orgs/{org_id}/tasin-settings/users', auth=None)
def list_system_users(request, org_id: int):
    org = _get_org(request, org_id)
    User = get_user_model()
    memberships = Membership.objects.filter(organization=org).select_related('user')
    out = []
    for m in memberships:
        out.append({
            'id': str(m.user.pk),
            'name': m.user.get_full_name() or m.user.username,
            'username': m.user.username,
            'email': m.user.email,
            'role': m.role,
            'isActive': m.user.is_active,
            'dateJoined': m.user.date_joined.isoformat() if hasattr(m.user, 'date_joined') and m.user.date_joined else ''
        })
    if not out:
        # Fallback to superusers/all users if no membership records
        for u in User.objects.all()[:10]:
            out.append({
                'id': str(u.pk),
                'name': u.get_full_name() or u.username,
                'username': u.username,
                'email': u.email,
                'role': 'admin' if u.is_superuser else 'operations',
                'isActive': u.is_active,
                'dateJoined': u.date_joined.isoformat() if hasattr(u, 'date_joined') and u.date_joined else ''
            })
    return out

@router.post('/orgs/{org_id}/tasin-settings/users', auth=None)
def create_system_user(request, org_id: int, payload: UserCreateIn):
    org = _get_org(request, org_id)
    User = get_user_model()
    if User.objects.filter(username=payload.username.strip()).exists():
        raise ApiError(409, f"User with username '{payload.username}' already exists")
    with transaction.atomic():
        user = User.objects.create_user(
            username=payload.username.strip(),
            email=payload.email.strip(),
            password=payload.password
        )
        Membership.objects.create(
            organization=org,
            user=user,
            role=payload.role
        )
    return {'id': str(user.pk), 'username': user.username, 'role': payload.role, 'success': True}

@router.delete('/orgs/{org_id}/tasin-settings/users/{user_id}', auth=None)
def delete_system_user(request, org_id: int, user_id: int):
    org = _get_org(request, org_id)
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if not user:
        raise ApiError(404, 'User not found')
    Membership.objects.filter(organization=org, user=user).delete()
    user.is_active = False
    user.save()
    return {'id': str(user_id), 'success': True}

# ─── MODULE 14: ADVANCED REPORTING ENDPOINTS ──────────────────────────────────

@router.get('/orgs/{org_id}/tasin-reports/vehicle-profitability', auth=None)
def report_vehicle_profitability(request, org_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None):
    org = _get_org(request, org_id)
    sd = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
    ed = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
    return calculate_vehicle_profitability(org, sd, ed)

@router.get('/orgs/{org_id}/tasin-reports/document-expiries', auth=None)
def report_document_expiries(request, org_id: int, filter: Optional[str] = None):
    org = _get_org(request, org_id)
    return get_document_expiries_report(org, filter)

@router.get('/orgs/{org_id}/tasin-reports/account-ledger', auth=None)
def report_account_ledger(request, org_id: int, account_id: Optional[int] = None, start_date: Optional[str] = None, end_date: Optional[str] = None):
    org = _get_org(request, org_id)
    sd = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
    ed = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
    return get_account_ledger_report(org, account_id, sd, ed)

# ─── RBAC: PERMISSION VERIFICATION ENDPOINT ───────────────────────────────────

@router.get('/orgs/{org_id}/rbac/permissions', auth=None)
def get_my_permissions(request, org_id: int):
    org = _get_org(request, org_id)
    return get_user_permissions_payload(request.user if hasattr(request, 'user') else None, org)

# ─── MODULE 04: DRIVER ATTENDANCE REGISTER ────────────────────────────────────

@router.get('/orgs/{org_id}/driver-attendance', auth=None)
def list_driver_attendance(request, org_id: int, year: Optional[int] = None, month: Optional[int] = None):
    org = _get_org(request, org_id)
    today = date.today()
    y = year or today.year
    m = month or today.month
    return get_driver_attendance_register(org, y, m)

@router.post('/orgs/{org_id}/driver-attendance', auth=None)
def record_attendance(request, org_id: int, payload: AttendanceIn):
    org = _get_org(request, org_id)
    driver = Party.objects.filter(pk=payload.driver_id, organization=org).first()
    if not driver:
        raise ApiError(404, 'Driver not found')
    att, created = DriverAttendance.objects.update_or_create(
        organization=org,
        driver=driver,
        date=payload.date,
        defaults={
            'status': payload.status,
            'duty_id': payload.duty_id,
            'notes': payload.notes or ''
        }
    )
    return {'id': str(att.pk), 'driver': driver.name, 'date': att.date.isoformat(), 'status': att.status, 'success': True}

# ─── MODULE 06: TRIP DISPATCH VIA SMS / WHATSAPP ─────────────────────────────

@router.post('/orgs/{org_id}/duties/{duty_id}/dispatch', auth=None)
def dispatch_duty(request, org_id: int, duty_id: int, payload: DispatchIn):
    org = _get_org(request, org_id)
    duty = Duty.objects.filter(pk=duty_id, organization=org).first()
    if not duty:
        raise ApiError(404, 'Duty not found')
    return dispatch_duty_trip_sheet(duty, payload.channel)

@router.get('/orgs/{org_id}/duties/{duty_id}/dispatches', auth=None)
def get_duty_dispatches(request, org_id: int, duty_id: int):
    org = _get_org(request, org_id)
    qs = TripDispatch.objects.filter(duty_id=duty_id).order_by('-sent_at')
    return [{
        'id': str(d.pk),
        'channel': d.channel,
        'driverPhone': d.driver_phone,
        'status': d.status,
        'sentAt': d.sent_at.isoformat(),
        'providerId': d.provider_id,
        'payload': d.dispatch_payload
    } for d in qs]

# ─── MODULE 08: SPARE PARTS INVENTORY ─────────────────────────────────────────

@router.get('/orgs/{org_id}/spare-parts', auth=None)
def list_spare_parts(request, org_id: int):
    org = _get_org(request, org_id)
    qs = SparePart.objects.filter(organization=org).order_by('name')
    return [{
        'id': str(p.pk),
        'partCode': p.part_code,
        'name': p.name,
        'category': p.category,
        'unit': p.unit,
        'quantity': float(p.quantity),
        'costPerUnit': float(p.cost_per_unit),
        'supplier': p.supplier,
        'lowStockThreshold': float(p.low_stock_threshold),
        'isLowStock': p.quantity <= p.low_stock_threshold,
        'notes': p.notes
    } for p in qs]

@router.post('/orgs/{org_id}/spare-parts', auth=None)
def create_or_update_spare_part(request, org_id: int, payload: SparePartIn):
    org = _get_org(request, org_id)
    p, created = SparePart.objects.update_or_create(
        organization=org,
        part_code=payload.part_code.strip(),
        defaults={
            'name': payload.name.strip(),
            'category': payload.category,
            'unit': payload.unit,
            'quantity': payload.quantity,
            'cost_per_unit': payload.cost_per_unit,
            'supplier': payload.supplier or '',
            'low_stock_threshold': payload.low_stock_threshold,
            'notes': payload.notes or ''
        }
    )
    return {'id': str(p.pk), 'partCode': p.part_code, 'name': p.name, 'quantity': float(p.quantity), 'success': True}

@router.post('/orgs/{org_id}/spare-parts/{part_id}/consume', auth=None)
def consume_spare_part(request, org_id: int, part_id: int, payload: PartConsumeIn):
    org = _get_org(request, org_id)
    part = SparePart.objects.filter(pk=part_id, organization=org).first()
    if not part:
        raise ApiError(404, 'Spare part not found')
    mnt = MaintenanceRecord.objects.filter(pk=payload.maintenance_id, organization=org).first()
    if not mnt:
        raise ApiError(404, 'Maintenance record not found')
        
    with transaction.atomic():
        part.quantity -= payload.quantity
        part.save()
        total_c = payload.quantity * part.cost_per_unit
        c = PartConsumption.objects.create(
            maintenance_record=mnt,
            spare_part=part,
            quantity=payload.quantity,
            unit_cost=part.cost_per_unit,
            total_cost=total_c,
            date=payload.date or date.today()
        )
        mnt.parts_cost += total_c
        mnt.total_cost += total_c
        mnt.save()
        
    return {'id': str(c.pk), 'remainingStock': float(part.quantity), 'costAdded': float(total_c), 'success': True}

# ─── MODULE 11: DEBTOR FOLLOW-UP MANAGEMENT ───────────────────────────────────

@router.get('/orgs/{org_id}/due-followups', auth=None)
def list_due_followups(request, org_id: int, party_id: Optional[int] = None, status: Optional[str] = None):
    org = _get_org(request, org_id)
    qs = DueFollowUp.objects.filter(organization=org).select_related('party')
    if party_id:
        qs = qs.filter(party_id=party_id)
    if status and status != 'all':
        qs = qs.filter(status=status)
    return [{
        'id': str(f.pk),
        'partyId': str(f.party_id),
        'partyName': f.party.name,
        'partyType': f.party_type,
        'followUpDate': f.follow_up_date.isoformat(),
        'contactMethod': f.contact_method,
        'notes': f.notes,
        'nextFollowUpDate': f.next_follow_up_date.isoformat() if f.next_follow_up_date else None,
        'status': f.status,
        'responsibleStaff': f.responsible_staff
    } for f in qs]

@router.post('/orgs/{org_id}/due-followups', auth=None)
def create_due_followup(request, org_id: int, payload: DueFollowUpIn):
    org = _get_org(request, org_id)
    party = Party.objects.filter(pk=payload.party_id, organization=org).first()
    if not party:
        raise ApiError(404, 'Party not found')
    f = DueFollowUp.objects.create(
        organization=org,
        party=party,
        party_type=payload.party_type,
        follow_up_date=payload.follow_up_date,
        contact_method=payload.contact_method,
        notes=payload.notes,
        next_follow_up_date=payload.next_follow_up_date,
        responsible_staff=payload.responsible_staff or '',
        status=payload.status
    )
    return {'id': str(f.pk), 'success': True}

@router.patch('/orgs/{org_id}/due-followups/{followup_id}', auth=None)
def update_due_followup_status(request, org_id: int, followup_id: int, status: str):
    org = _get_org(request, org_id)
    f = DueFollowUp.objects.filter(pk=followup_id, organization=org).first()
    if not f:
        raise ApiError(404, 'Follow-up not found')
    f.status = status
    f.save()
    return {'id': str(f.pk), 'status': f.status, 'success': True}

# ─── MODULE 14: CUSTOM REMINDERS & TO-DOS ────────────────────────────────────

@router.get('/orgs/{org_id}/custom-reminders', auth=None)
def list_custom_reminders(request, org_id: int):
    org = _get_org(request, org_id)
    qs = CustomReminder.objects.filter(organization=org).order_by('reminder_date')
    return [{
        'id': str(r.pk),
        'title': r.title,
        'reminderDate': r.reminder_date.isoformat(),
        'reminderTime': r.reminder_time,
        'description': r.description,
        'relatedEntityType': r.related_entity_type,
        'relatedEntityId': r.related_entity_id,
        'assignedStaff': r.assigned_staff,
        'status': r.status
    } for r in qs]

@router.post('/orgs/{org_id}/custom-reminders', auth=None)
def create_custom_reminder(request, org_id: int, payload: CustomReminderIn):
    org = _get_org(request, org_id)
    r = CustomReminder.objects.create(
        organization=org,
        title=payload.title,
        reminder_date=payload.reminder_date,
        reminder_time=payload.reminder_time,
        description=payload.description or '',
        related_entity_type=payload.related_entity_type,
        related_entity_id=payload.related_entity_id or '',
        assigned_staff=payload.assigned_staff or '',
        status=payload.status
    )
    return {'id': str(r.pk), 'title': r.title, 'success': True}

@router.patch('/orgs/{org_id}/custom-reminders/{reminder_id}', auth=None)
def update_custom_reminder(request, org_id: int, reminder_id: int, status: str):
    org = _get_org(request, org_id)
    r = CustomReminder.objects.filter(pk=reminder_id, organization=org).first()
    if not r: raise ApiError(404, 'Reminder not found')
    r.status = status
    r.save()
    return {'id': str(r.pk), 'status': r.status, 'success': True}

@router.delete('/orgs/{org_id}/custom-reminders/{reminder_id}', auth=None)
def delete_custom_reminder(request, org_id: int, reminder_id: int):
    org = _get_org(request, org_id)
    CustomReminder.objects.filter(pk=reminder_id, organization=org).delete()
    return {'success': True}

# ─── MODULE 09: AGREEMENT RENEWAL WORKFLOW ───────────────────────────────────

@router.post('/orgs/{org_id}/agreements/{agreement_id}/renew', auth=None)
def renew_agreement_endpoint(request, org_id: int, agreement_id: int, payload: RenewAgreementIn):
    org = _get_org(request, org_id)
    agreement = Agreement.objects.filter(pk=agreement_id, organization=org).first()
    if not agreement:
        raise ApiError(404, 'Agreement not found')
    return renew_agreement_service(agreement, payload.new_start_date, payload.new_end_date, payload.new_monthly_rent)

# ─── MODULE 10: BANK RECONCILIATION ──────────────────────────────────────────

@router.post('/orgs/{org_id}/bank-reconciliation/match', auth=None)
def match_bank_statement(request, org_id: int, payload: BankReconIn):
    org = _get_org(request, org_id)
    rows_data = [r.dict() for r in payload.rows]
    return reconcile_bank_statement(org, payload.account_id, rows_data)

# ─── MODULE 13: AD-HOC CUSTOM REPORT BUILDER ─────────────────────────────────

@router.post('/orgs/{org_id}/reports/custom-builder', auth=None)
def custom_report_builder_endpoint(request, org_id: int, payload: CustomReportIn):
    org = _get_org(request, org_id)
    return build_custom_report(org, payload.module, payload.fields, payload.start_date, payload.end_date, payload.status_filter)

# ─── MODULE 15: SECURE DATABASE BACKUP GENERATION ────────────────────────────

@router.get('/orgs/{org_id}/backup/download', auth=None)
def download_database_backup(request, org_id: int):
    org = _get_org(request, org_id)
    backup_json = generate_organization_backup(org)
    from django.http import HttpResponse
    resp = HttpResponse(backup_json, content_type='application/json')
    filename = f"tasin_backup_{org.pk}_{date.today().isoformat()}.json"
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp

# ─── DOCUMENT UPLOADS & FILE STORAGE ─────────────────────────────────────────

@router.post('/orgs/{org_id}/documents/upload', auth=None)
def upload_private_document(request, org_id: int, payload: DocumentUploadIn):
    import base64
    import os
    from django.conf import settings
    org = _get_org(request, org_id)
    
    # Decode base64
    try:
        file_bytes = base64.b64decode(payload.file_base64)
    except Exception:
        raise ApiError(400, 'Invalid base64 payload')
        
    # Safe storage directory
    media_root = getattr(settings, 'PRIVATE_MEDIA_ROOT', os.path.join(settings.BASE_DIR, 'private_media'))
    org_dir = os.path.join(media_root, f'org_{org.pk}', payload.entity_type.lower())
    os.makedirs(org_dir, exist_ok=True)
    
    clean_filename = f"{int(datetime.now().timestamp())}_{payload.file_name.replace(' ', '_')}"
    file_path = os.path.join(org_dir, clean_filename)
    with open(file_path, 'wb') as f:
        f.write(file_bytes)
        
    doc = PrivateDocument.objects.create(
        organization=org,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        document_type=payload.document_type,
        file_name=payload.file_name,
        file_path=os.path.relpath(file_path, settings.BASE_DIR),
        file_size=len(file_bytes),
        mime_type=payload.mime_type
    )
    return {
        'id': str(doc.pk),
        'fileName': doc.file_name,
        'documentType': doc.document_type,
        'fileSize': doc.file_size,
        'uploadedAt': doc.uploaded_at.isoformat(),
        'success': True
    }

@router.get('/orgs/{org_id}/documents', auth=None)
def list_private_documents(request, org_id: int, entity_type: str, entity_id: str):
    org = _get_org(request, org_id)
    qs = PrivateDocument.objects.filter(organization=org, entity_type=entity_type, entity_id=entity_id)
    return [{
        'id': str(d.pk),
        'fileName': d.file_name,
        'documentType': d.document_type,
        'fileSize': d.file_size,
        'mimeType': d.mime_type,
        'uploadedAt': d.uploaded_at.isoformat()
    } for d in qs]


