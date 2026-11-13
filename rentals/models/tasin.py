from django.db import models
from django.conf import settings
from .organizations import Organization, Branch
from .parties import Party, Vehicle

class VehicleDetail(models.Model):
    """Extended details for Vehicle (Module 03 & 12)."""
    CATEGORY_CHOICES = [
        ('Sedan', 'Sedan'), ('SUV', 'SUV'), ('Microbus', 'Microbus'),
        ('Pickup', 'Pickup'), ('Van', 'Van'), ('Truck', 'Truck')
    ]
    STATUS_CHOICES = [
        ('Available', 'Available'), ('On Duty', 'On Duty'),
        ('In Garage', 'In Garage'), ('Inactive', 'Inactive')
    ]
    vehicle = models.OneToOneField(Vehicle, on_delete=models.CASCADE, related_name='detail')
    brand = models.CharField(max_length=60, default='Toyota')
    model = models.CharField(max_length=80, default='')
    year = models.PositiveIntegerField(default=2021)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='Sedan')
    color = models.CharField(max_length=40, default='White')
    chassis_no = models.CharField(max_length=80, blank=True, default='')
    engine_no = models.CharField(max_length=80, blank=True, default='')
    registration_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Available')
    monthly_rent = models.DecimalField(max_digits=12, decimal_places=2, default=50000)
    agreement_start = models.DateField(null=True, blank=True)
    agreement_end = models.DateField(null=True, blank=True)
    assigned_driver = models.ForeignKey(Party, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_vehicle_details')
    mobil_count = models.PositiveIntegerField(default=0)
    last_mobil_date = models.DateField(null=True, blank=True)
    photo_url = models.CharField(max_length=255, blank=True, default='')

class PartyDetail(models.Model):
    """Extended details for Owner, Driver, and Client (Modules 02, 04, 05)."""
    party = models.OneToOneField(Party, on_delete=models.CASCADE, related_name='detail')
    alt_mobile = models.CharField(max_length=32, blank=True, default='')
    nid = models.CharField(max_length=40, blank=True, default='')
    present_address = models.TextField(blank=True, default='')
    permanent_address = models.TextField(blank=True, default='')
    # Owner & Driver Bank Details
    bank_name = models.CharField(max_length=120, blank=True, default='')
    account_name = models.CharField(max_length=120, blank=True, default='')
    account_number = models.CharField(max_length=64, blank=True, default='')
    branch_name = models.CharField(max_length=120, blank=True, default='')
    account_type = models.CharField(max_length=60, blank=True, default='Savings')
    joining_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default='Active') # Active / Inactive
    # Driver specific
    dob = models.DateField(null=True, blank=True)
    license_no = models.CharField(max_length=60, blank=True, default='')
    license_type = models.CharField(max_length=60, blank=True, default='Professional')
    license_expiry = models.DateField(null=True, blank=True)
    emergency_contact = models.CharField(max_length=60, blank=True, default='')
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    daily_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    advance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fuel_advance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gas_advance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reference_info = models.TextField(blank=True, default='')
    photo_url = models.CharField(max_length=255, blank=True, default='')
    nid_doc_url = models.CharField(max_length=255, blank=True, default='')
    trade_license_doc_url = models.CharField(max_length=255, blank=True, default='')
    total_paid_driver = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    assigned_vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_driver_parties')
    # Owner specific
    total_vehicles = models.PositiveIntegerField(default=1)
    total_payable_owner = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_paid_owner = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Client specific
    contact_person = models.CharField(max_length=120, blank=True, default='')
    billing_address = models.TextField(blank=True, default='')
    trade_license = models.CharField(max_length=80, blank=True, default='')
    client_type = models.CharField(max_length=30, default='Company') # Individual, Company, Government
    total_receivable_client = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_collected_client = models.DecimalField(max_digits=12, decimal_places=2, default=0)

class Duty(models.Model):
    """Daily Operations / Duty Management (Module 06)."""
    STATUS_CHOICES = [
        ('Scheduled', 'Scheduled'), ('Running', 'Running'),
        ('Completed', 'Completed'), ('Cancelled', 'Cancelled')
    ]
    DUTY_TYPE_CHOICES = [
        ('Monthly', 'Monthly'), ('Daily', 'Daily'),
        ('Trip', 'Trip'), ('Outstation', 'Outstation')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='duties')
    duty_id = models.CharField(max_length=32, db_index=True) # e.g. DTY-001
    date = models.DateField()
    client = models.ForeignKey(Party, null=True, blank=True, on_delete=models.SET_NULL, related_name='duties_as_client')
    client_name = models.CharField(max_length=180)
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name='duties')
    vehicle_reg = models.CharField(max_length=64)
    driver = models.ForeignKey(Party, null=True, blank=True, on_delete=models.SET_NULL, related_name='duties_as_driver')
    driver_name = models.CharField(max_length=180)
    pickup_location = models.CharField(max_length=220)
    destination = models.CharField(max_length=220)
    start_time = models.CharField(max_length=20)
    end_time = models.CharField(max_length=20, blank=True, default='')
    duty_type = models.CharField(max_length=20, choices=DUTY_TYPE_CHOICES, default='Daily')
    customer_contact = models.CharField(max_length=40, blank=True, default='')
    description = models.TextField(blank=True, default='')
    # Financial breakdown
    agreed_rent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fuel_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    overtime_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    driver_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    additional_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Scheduled')
    is_invoiced = models.BooleanField(default=False)
    invoice = models.ForeignKey('TasinInvoice', null=True, blank=True, on_delete=models.SET_NULL, related_name='duties')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        ordering = ['-date', '-id']

class Garage(models.Model):
    """Garage Management (Module 08)."""
    GARAGE_TYPE_CHOICES = [
        ('General', 'General'), ('AC Specialist', 'AC Specialist'),
        ('Body Works', 'Body Works'), ('Engine', 'Engine'), ('Electrical', 'Electrical')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='garages')
    garage_id = models.CharField(max_length=32, db_index=True) # e.g. GRG-001
    name = models.CharField(max_length=180)
    contact_person = models.CharField(max_length=180, blank=True, default='')
    mobile = models.CharField(max_length=40)
    address = models.TextField(blank=True, default='')
    garage_type = models.CharField(max_length=40, choices=GARAGE_TYPE_CHOICES, default='General')
    status = models.CharField(max_length=20, default='Active') # Active / Inactive
    total_bill = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

class MaintenanceRecord(models.Model):
    """Maintenance Record (Module 08)."""
    STATUS_CHOICES = [
        ('Pending', 'Pending'), ('In Progress', 'In Progress'),
        ('Completed', 'Completed'), ('Delivered', 'Delivered')
    ]
    CATEGORY_CHOICES = [
        ('Engine', 'Engine'), ('Gearbox', 'Gearbox'), ('Suspension', 'Suspension'),
        ('Brake', 'Brake'), ('Electrical', 'Electrical'), ('AC', 'AC'),
        ('Tyre', 'Tyre'), ('Battery', 'Battery'), ('Oil Change', 'Oil Change'),
        ('General Service', 'General Service'), ('Other', 'Other')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='tasin_maintenance_records')
    maintenance_id = models.CharField(max_length=32, db_index=True) # e.g. MNT-001
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name='tasin_maintenances')
    vehicle_reg = models.CharField(max_length=64)
    garage = models.ForeignKey(Garage, null=True, blank=True, on_delete=models.SET_NULL, related_name='maintenances')
    garage_name = models.CharField(max_length=180)
    date = models.DateField()
    problem_description = models.TextField()
    service_type = models.CharField(max_length=40, choices=CATEGORY_CHOICES, default='General Service')
    parts_details = models.TextField(blank=True, default='')
    labour_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    parts_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expected_delivery = models.DateField(null=True, blank=True)
    actual_delivery = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)

class VehiclePaper(models.Model):
    """Vehicle Papers & Expiry Management (Modules 03, 09, 14, M15)."""
    PAPER_TYPE_CHOICES = [
        ('registration', 'Registration Paper'), ('fitness', 'Fitness Certificate'),
        ('tax_token', 'Tax Token'), ('insurance', 'Insurance'),
        ('route_permit', 'Route Permit'), ('custom', 'Other Document')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='papers')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='papers')
    paper_type = models.CharField(max_length=40, choices=PAPER_TYPE_CHOICES)
    paper_number = models.CharField(max_length=80, blank=True, default='')
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default='Valid') # Valid, Expiring Soon, Expired
    document_url = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

class FinancialAccount(models.Model):
    """Cash & Bank Accounts Management (Module 10)."""
    ACCOUNT_TYPE_CHOICES = [('Cash', 'Cash'), ('Bank', 'Bank'), ('Mobile Banking', 'Mobile Banking')]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='financial_accounts')
    account_id = models.CharField(max_length=32, db_index=True) # e.g. ACC-001
    name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=64, blank=True, default='')
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default='Bank')
    bank_name = models.CharField(max_length=120, blank=True, default='')
    branch_name = models.CharField(max_length=120, blank=True, default='')
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    current_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AccountTransaction(models.Model):
    """Transactions & Transfers (Module 10)."""
    TXN_TYPE_CHOICES = [('Income', 'Income'), ('Expense', 'Expense'), ('Transfer', 'Transfer')]
    PAYMENT_METHOD_CHOICES = [('Cash', 'Cash'), ('Bank Transfer', 'Bank Transfer'), ('Mobile Banking', 'Mobile Banking')]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='financial_transactions')
    transaction_id = models.CharField(max_length=32, db_index=True) # e.g. TXN-001
    date = models.DateField()
    transaction_type = models.CharField(max_length=20, choices=TXN_TYPE_CHOICES)
    category = models.CharField(max_length=80)
    related_person = models.CharField(max_length=180, blank=True, default='')
    vehicle_reg = models.CharField(max_length=64, blank=True, default='')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=40, choices=PAYMENT_METHOD_CHOICES, default='Cash')
    reference = models.CharField(max_length=120, blank=True, default='')
    description = models.TextField(blank=True, default='')
    from_account = models.ForeignKey(FinancialAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name='outgoing_transfers')
    to_account = models.ForeignKey(FinancialAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name='incoming_transfers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

class SystemAlert(models.Model):
    """Notifications & Alerts (Module 14)."""
    ALERT_TYPE_CHOICES = [
        ('payment', 'Payment Reminder'), ('agreement', 'Agreement Alert'),
        ('document', 'Document Expiry'), ('maintenance', 'Maintenance Alert'),
        ('custom', 'Custom Reminder')
    ]
    SEVERITY_CHOICES = [('info', 'Info'), ('warning', 'Warning'), ('danger', 'Danger')]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='system_alerts')
    alert_id = models.CharField(max_length=32, db_index=True) # e.g. ALT-001
    alert_type = models.CharField(max_length=30, choices=ALERT_TYPE_CHOICES, default='custom')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='info')
    title = models.CharField(max_length=220)
    detail = models.TextField(blank=True, default='')
    date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    related_id = models.CharField(max_length=64, blank=True, default='')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

class CompanyProfile(models.Model):
    """Company Profile & System Settings (Module 15)."""
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='company_profile')
    company_name = models.CharField(max_length=180, default='TASIN ENTERPRISE')
    company_logo = models.CharField(max_length=255, blank=True, default='')
    address = models.TextField(default='Level 8, Motijheel C/A, Dhaka-1000, Bangladesh')
    phone = models.CharField(max_length=40, default='+880 1711-234567')
    email = models.EmailField(default='info@tasinenterprise.com')
    website = models.CharField(max_length=120, blank=True, default='https://tasinenterprise.com')
    bin = models.CharField(max_length=50, blank=True, default='BIN-002918274-0101')
    tin = models.CharField(max_length=50, blank=True, default='TIN-992817263541')
    details = models.TextField(blank=True, default='TASIN ENTERPRISE – Premium Rent a Car Management Software')
    currency = models.CharField(max_length=10, default='BDT')
    timezone = models.CharField(max_length=40, default='Asia/Dhaka')
    date_format = models.CharField(max_length=20, default='YYYY-MM-DD')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class TasinInvoice(models.Model):
    """Invoicing Engine (Module 05 & 06)."""
    STATUS_CHOICES = [
        ('Draft', 'Draft'), ('Sent', 'Sent'),
        ('Partially Paid', 'Partially Paid'), ('Paid', 'Paid'),
        ('Overdue', 'Overdue'), ('Cancelled', 'Cancelled')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='tasin_invoices')
    invoice_number = models.CharField(max_length=64, db_index=True) # e.g. INV-2026-0001
    client = models.ForeignKey(Party, on_delete=models.CASCADE, related_name='tasin_invoices')
    client_name = models.CharField(max_length=180)
    issue_date = models.DateField()
    due_date = models.DateField()
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Sent')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'invoice_number'], name='unique_tasin_invoice_number')
        ]

    @property
    def remaining_due(self):
        return max(0, float(self.total_amount - self.paid_amount))

class TasinInvoiceItem(models.Model):
    """Invoice Line Items (Module 05)."""
    invoice = models.ForeignKey(TasinInvoice, on_delete=models.CASCADE, related_name='items')
    duty = models.ForeignKey('Duty', null=True, blank=True, on_delete=models.SET_NULL, related_name='invoice_items')
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

class TasinInvoicePayment(models.Model):
    """Invoice Payments & Partial Payments Ledger (Module 05 & 10)."""
    invoice = models.ForeignKey(TasinInvoice, on_delete=models.CASCADE, related_name='payments')
    payment_id = models.CharField(max_length=64, db_index=True) # e.g. PAY-INV-001
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=40, default='Cash')
    account = models.ForeignKey(FinancialAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name='invoice_payments')
    reference = models.CharField(max_length=120, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class DriverAttendance(models.Model):
    """Driver Attendance Register (Module 04)."""
    STATUS_CHOICES = [
        ('Present', 'Present'),
        ('Absent', 'Absent'),
        ('On Duty', 'On Duty'),
        ('Leave', 'Leave')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='driver_attendances')
    driver = models.ForeignKey(Party, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Present')
    duty = models.ForeignKey(Duty, null=True, blank=True, on_delete=models.SET_NULL, related_name='attendances')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', 'driver__name']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'driver', 'date'], name='unique_driver_daily_attendance')
        ]


class SparePart(models.Model):
    """Spare Parts & Mobil Inventory (Module 08)."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='spare_parts')
    part_code = models.CharField(max_length=40, db_index=True) # e.g. PRT-MOB-01
    name = models.CharField(max_length=180)
    category = models.CharField(max_length=80, default='Mobil/Oil') # Mobil/Oil, Tyre, Brake, Filter, Battery, Electrical, Other
    unit = models.CharField(max_length=30, default='Litre') # Litre, Piece, Set
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cost_per_unit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    supplier = models.CharField(max_length=180, blank=True, default='')
    low_stock_threshold = models.DecimalField(max_digits=10, decimal_places=2, default=5)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'part_code'], name='unique_org_part_code')
        ]


class PartConsumption(models.Model):
    """Parts Consumed in Maintenance (Module 08)."""
    maintenance_record = models.ForeignKey(MaintenanceRecord, on_delete=models.CASCADE, related_name='consumptions')
    spare_part = models.ForeignKey(SparePart, on_delete=models.CASCADE, related_name='consumptions')
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)


class DueFollowUp(models.Model):
    """Debtor Follow-up Management (Module 11)."""
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Completed', 'Completed'),
        ('Overdue', 'Overdue')
    ]
    METHOD_CHOICES = [
        ('Phone Call', 'Phone Call'),
        ('WhatsApp', 'WhatsApp'),
        ('In Person', 'In Person'),
        ('Email', 'Email')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='due_followups')
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name='followups')
    party_type = models.CharField(max_length=20, default='Customer') # Customer, Owner, Driver, Garage
    follow_up_date = models.DateField()
    contact_method = models.CharField(max_length=30, choices=METHOD_CHOICES, default='Phone Call')
    notes = models.TextField()
    next_follow_up_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    responsible_staff = models.CharField(max_length=120, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-follow_up_date', '-id']


class CustomReminder(models.Model):
    """Custom Reminders & To-Dos (Module 14)."""
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Completed', 'Completed'),
        ('Overdue', 'Overdue')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='custom_reminders')
    title = models.CharField(max_length=220)
    reminder_date = models.DateField()
    reminder_time = models.CharField(max_length=20, blank=True, default='09:00 AM')
    description = models.TextField(blank=True, default='')
    related_entity_type = models.CharField(max_length=40, blank=True, default='General') # Vehicle, Driver, Owner, Client, General
    related_entity_id = models.CharField(max_length=64, blank=True, default='')
    assigned_staff = models.CharField(max_length=120, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['reminder_date', 'id']


class TripDispatch(models.Model):
    """Trip Dispatch via SMS/WhatsApp (Module 06)."""
    CHANNEL_CHOICES = [
        ('SMS', 'SMS'),
        ('WhatsApp', 'WhatsApp')
    ]
    STATUS_CHOICES = [
        ('Sent', 'Sent'),
        ('Delivered', 'Delivered'),
        ('Failed', 'Failed'),
        ('Queued', 'Queued')
    ]
    duty = models.ForeignKey(Duty, on_delete=models.CASCADE, related_name='dispatches')
    driver_phone = models.CharField(max_length=40)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default='SMS')
    dispatch_payload = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Sent')
    provider_id = models.CharField(max_length=120, blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    sent_at = models.DateTimeField(auto_now_add=True)


class PrivateDocument(models.Model):
    """Secure File Storage for NID, Licenses, Papers, Vouchers & Bills (All Modules)."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='private_documents')
    entity_type = models.CharField(max_length=40) # Owner, Driver, Vehicle, Client, Maintenance, Expense, Transaction, Agreement, Company
    entity_id = models.CharField(max_length=64)
    document_type = models.CharField(max_length=60) # NID, Photo, License, Paper, Trade_License, Voucher, Bill, Agreement, Logo
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500) # relative path in private media or storage
    file_size = models.PositiveIntegerField(default=0) # in bytes
    mime_type = models.CharField(max_length=100, default='application/octet-stream')
    uploaded_by = models.CharField(max_length=120, blank=True, default='')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']


class UserProfile(models.Model):
    """Extended Employee Profile and Administrative Delegation (Module 01 & 15)."""
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Inactive', 'Inactive'),
        ('Suspended', 'Suspended')
    ]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='tasin_profile')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='user_profiles')
    phone = models.CharField(max_length=40, blank=True, default='')
    designation = models.CharField(max_length=120, blank=True, default='Operations Staff')
    department = models.CharField(max_length=120, blank=True, default='Operations')
    employee_id = models.CharField(max_length=40, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Active')
    is_main_admin = models.BooleanField(default=False)
    can_manage_users = models.BooleanField(default=False)
    can_approve_requests = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.username} - {self.designation} ({self.status})"


class UserModulePermission(models.Model):
    """Dynamic Module-Level and Granular Action-Level Authorization (All 15 Modules)."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='user_module_permissions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='module_permissions')
    module = models.CharField(max_length=60, db_index=True) # e.g. 'dashboard', 'vehicles', etc.
    can_view = models.BooleanField(default=True)
    can_create = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    can_export = models.BooleanField(default=False)
    can_approve = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['module']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'user', 'module'], name='unique_org_user_module_perm')
        ]

    def __str__(self):
        return f"{self.user.username} | {self.module} [V:{self.can_view} C:{self.can_create} E:{self.can_edit} D:{self.can_delete}]"


class AccessRequest(models.Model):
    """Employee Workspace / Module Access Request & Approval Workflow."""
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Revoked', 'Revoked')
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='access_requests')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='access_requests')
    requested_module = models.CharField(max_length=60, db_index=True)
    can_view = models.BooleanField(default=True)
    can_create = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    can_export = models.BooleanField(default=False)
    can_approve = models.BooleanField(default=False)
    reason = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending', db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_access_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"REQ-{self.id} | {self.user.username} -> {self.requested_module} ({self.status})"




