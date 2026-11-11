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
    VehiclePaper, FinancialAccount, AccountTransaction, SystemAlert, CompanyProfile
)
from ..services.tasin import get_dashboard_kpis, get_or_create_company_profile

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
    return get_dashboard_kpis(org, target, period=period_mode)

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

# ─── MODULE 05: CLIENTS & COMPANIES MANAGEMENT ───────────────────────────────

@router.get('/orgs/{org_id}/tasin-clients', auth=None)
def list_clients(request, org_id: int):
    org = _get_org(request, org_id)
    clients = Party.objects.filter(organization=org, kind=Party.Kind.CUSTOMER).select_related('detail')
    out = []
    for c in clients:
        det = getattr(c, 'detail', None)
        rec = det.total_receivable_client if det else D(90000)
        col = det.total_collected_client if det else D(60000)
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
            'due': float(rec - col)
        })
    return out

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
    # Update vehicle status to 'In Garage' if in progress
    v = Vehicle.objects.filter(organization=org, registration=payload.vehicle_reg.strip()).first()
    if v and payload.status in ('Pending', 'In Progress'):
        det, _ = VehicleDetail.objects.get_or_create(vehicle=v)
        det.status = 'In Garage'
        det.save()
    return {'id': str(m.pk), 'maintenanceId': m.maintenance_id, 'totalCost': float(m.total_cost), 'success': True}

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
        rec = det.total_receivable_client if det else D(100000)
        col = det.total_collected_client if det else D(70000)
        rem = rec - col
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
        pay = det.total_payable_owner if det else D(120000)
        paid = det.total_paid_owner if det else D(70000)
        rem = pay - paid
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
        sal = det.monthly_salary if det else D(20000)
        allow = det.daily_allowance if det else D(300)
        adv = det.advance if det else D(0)
        paid = det.total_paid_driver if det else D(15000)
        rem = sal - paid + adv
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
        rem = g.total_bill - g.total_paid
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
