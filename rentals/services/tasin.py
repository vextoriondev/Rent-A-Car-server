from decimal import Decimal
from datetime import date, datetime, timedelta
from django.db import transaction
from django.db.models import Sum, Count, Q
from ..models import (
    Organization, Party, Vehicle,
    VehicleDetail, PartyDetail, Duty, Garage, MaintenanceRecord,
    VehiclePaper, FinancialAccount, AccountTransaction, SystemAlert, CompanyProfile,
    TasinInvoice, TasinInvoiceItem, TasinInvoicePayment
)

D = Decimal

def get_or_create_company_profile(org: Organization):
    profile, _ = CompanyProfile.objects.get_or_create(
        organization=org,
        defaults={
            'company_name': 'TASIN ENTERPRISE',
            'address': 'Level 8, Motijheel C/A, Dhaka-1000, Bangladesh',
            'phone': '+880 1711-234567',
            'email': 'info@tasinenterprise.com',
            'website': 'https://tasinenterprise.com',
            'bin': 'BIN-002918274-0101',
            'tin': 'TIN-992817263541',
            'currency': 'BDT',
            'timezone': 'Asia/Dhaka',
        }
    )
    return profile

def get_dashboard_kpis(org: Organization, target_date=None, period='Monthly'):
    if not target_date:
        target_date = date.today()

    # 1. Vehicles
    vehicles = Vehicle.objects.filter(organization=org).select_related('detail')
    total_vehicles = vehicles.count()
    status_counts = {'Available': 0, 'On Duty': 0, 'In Garage': 0, 'Inactive': 0}
    for v in vehicles:
        st = getattr(v, 'detail', None) and v.detail.status or 'Available'
        status_counts[st] = status_counts.get(st, 0) + 1
    active_vehicles = total_vehicles - status_counts.get('Inactive', 0)

    # 2. Owners & Drivers
    owners = Party.objects.filter(organization=org, kind=Party.Kind.OWNER).select_related('detail')
    total_owners = owners.count()
    active_owners = sum(1 for o in owners if not getattr(o, 'detail', None) or o.detail.status == 'Active')

    drivers = Party.objects.filter(organization=org, kind=Party.Kind.DRIVER).select_related('detail')
    total_drivers = drivers.count()
    active_drivers = sum(1 for d in drivers if not getattr(d, 'detail', None) or d.detail.status == 'Active')
    unassigned_drivers = sum(1 for d in drivers if not getattr(d, 'detail', None) or not d.detail.assigned_vehicle)

    # 3. Financial Overview
    clients = Party.objects.filter(organization=org, kind=Party.Kind.CUSTOMER).select_related('detail')
    total_customer_receivable = sum((c.detail.total_receivable_client - c.detail.total_collected_client) for c in clients if getattr(c, 'detail', None))
    total_owner_payable = sum((o.detail.total_payable_owner - o.detail.total_paid_owner) for o in owners if getattr(o, 'detail', None))
    total_driver_payable = sum((d.detail.monthly_salary - d.detail.total_paid_driver + d.detail.advance) for d in drivers if getattr(d, 'detail', None))
    
    garages = Garage.objects.filter(organization=org)
    total_garage_payable = sum((g.total_bill - g.total_paid) for g in garages)

    accounts = FinancialAccount.objects.filter(organization=org, is_active=True)
    cash_balance = sum(a.current_balance for a in accounts if a.account_type == 'Cash')
    bank_balance = sum(a.current_balance for a in accounts if a.account_type in ('Bank', 'Mobile Banking'))

    # Duties for today
    today_duties = Duty.objects.filter(organization=org, date=target_date)
    running_duties = today_duties.filter(status='Running').count()
    completed_duties = today_duties.filter(status='Completed').count()
    scheduled_duties = today_duties.filter(status='Scheduled').count()
    today_revenue = sum(d.total_amount for d in today_duties.exclude(status='Cancelled'))

    # Expenses for today
    today_txns = AccountTransaction.objects.filter(organization=org, date=target_date, transaction_type='Expense')
    today_expenses = sum(t.amount for t in today_txns)

    # Overall totals
    all_income = AccountTransaction.objects.filter(organization=org, transaction_type='Income').aggregate(s=Sum('amount'))['s'] or D(0)
    all_expense = AccountTransaction.objects.filter(organization=org, transaction_type='Expense').aggregate(s=Sum('amount'))['s'] or D(0)

    # Actual DB Collections & Owner Payments (replaces hardcoded frontend multipliers)
    real_collections = AccountTransaction.objects.filter(
        organization=org, transaction_type='Income', category__icontains='Collection'
    ).aggregate(s=Sum('amount'))['s']
    if not real_collections:
        real_collections = sum((c.detail.total_collected_client) for c in clients if getattr(c, 'detail', None))
    total_collection = float(real_collections or all_income)

    real_owner_payments = sum((o.detail.total_paid_owner) for o in owners if getattr(o, 'detail', None))
    if not real_owner_payments:
        real_owner_payments = AccountTransaction.objects.filter(
            organization=org, transaction_type='Expense', category='Owner Payment'
        ).aggregate(s=Sum('amount'))['s'] or D(0)
    total_owner_payment = float(real_owner_payments)

    # Previous Month Comparison calculation
    cur_m = target_date.month
    cur_y = target_date.year
    prev_m = 12 if cur_m == 1 else cur_m - 1
    prev_y = cur_y - 1 if cur_m == 1 else cur_y
    cur_month_inc = AccountTransaction.objects.filter(organization=org, transaction_type='Income', date__year=cur_y, date__month=cur_m).aggregate(s=Sum('amount'))['s'] or D(0)
    prev_month_inc = AccountTransaction.objects.filter(organization=org, transaction_type='Income', date__year=prev_y, date__month=prev_m).aggregate(s=Sum('amount'))['s'] or D(0)
    cur_month_exp = AccountTransaction.objects.filter(organization=org, transaction_type='Expense', date__year=cur_y, date__month=cur_m).aggregate(s=Sum('amount'))['s'] or D(0)
    prev_month_exp = AccountTransaction.objects.filter(organization=org, transaction_type='Expense', date__year=prev_y, date__month=prev_m).aggregate(s=Sum('amount'))['s'] or D(0)
    
    rev_diff = float(cur_month_inc - prev_month_inc)
    rev_pct = round(((float(cur_month_inc - prev_month_inc) / float(prev_month_inc)) * 100), 1) if prev_month_inc > 0 else (100.0 if cur_month_inc > 0 else 0.0)

    # Ensure dynamic automated alerts are in database
    generate_system_alerts(org, target_date)

    # Alerts count
    unread_alerts = SystemAlert.objects.filter(organization=org, is_read=False).count()

    # Chart data based on selected period
    monthly_chart = []
    if period == 'Daily':
        for i in range(6, -1, -1):
            d = target_date - timedelta(days=i)
            d_inc = AccountTransaction.objects.filter(organization=org, transaction_type='Income', date=d).aggregate(s=Sum('amount'))['s'] or D(0)
            d_exp = AccountTransaction.objects.filter(organization=org, transaction_type='Expense', date=d).aggregate(s=Sum('amount'))['s'] or D(0)
            monthly_chart.append({
                'month': d.strftime('%a %d'),
                'income': float(d_inc),
                'expenses': float(d_exp),
                'profit': float(d_inc - d_exp)
            })
    elif period == 'Weekly':
        for i in range(3, -1, -1):
            w_start = target_date - timedelta(days=(i * 7 + 6))
            w_end = target_date - timedelta(days=(i * 7))
            w_inc = AccountTransaction.objects.filter(organization=org, transaction_type='Income', date__gte=w_start, date__lte=w_end).aggregate(s=Sum('amount'))['s'] or D(0)
            w_exp = AccountTransaction.objects.filter(organization=org, transaction_type='Expense', date__gte=w_start, date__lte=w_end).aggregate(s=Sum('amount'))['s'] or D(0)
            monthly_chart.append({
                'month': f'Wk {4 - i}',
                'income': float(w_inc),
                'expenses': float(w_exp),
                'profit': float(w_inc - w_exp)
            })
    elif period == 'Yearly':
        current_y = target_date.year
        for y in range(current_y - 2, current_y + 1):
            y_inc = AccountTransaction.objects.filter(organization=org, transaction_type='Income', date__year=y).aggregate(s=Sum('amount'))['s'] or D(0)
            y_exp = AccountTransaction.objects.filter(organization=org, transaction_type='Expense', date__year=y).aggregate(s=Sum('amount'))['s'] or D(0)
            monthly_chart.append({
                'month': str(y),
                'income': float(y_inc),
                'expenses': float(y_exp),
                'profit': float(y_inc - y_exp)
            })
    else:  # Monthly
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        base_month = target_date.month
        base_year = target_date.year
        for i in range(5, -1, -1):
            m = ((base_month - 1 - i) % 12) + 1
            y = base_year if (base_month - i) > 0 else base_year - 1
            m_inc = AccountTransaction.objects.filter(organization=org, transaction_type='Income', date__year=y, date__month=m).aggregate(s=Sum('amount'))['s'] or D(0)
            m_exp = AccountTransaction.objects.filter(organization=org, transaction_type='Expense', date__year=y, date__month=m).aggregate(s=Sum('amount'))['s'] or D(0)
            monthly_chart.append({
                'month': month_names[m - 1],
                'income': float(m_inc),
                'expenses': float(m_exp),
                'profit': float(m_inc - m_exp)
            })

    # Recent transactions
    recent_txns = AccountTransaction.objects.filter(organization=org)[:10]
    recent_list = [{
        'id': t.transaction_id,
        'date': t.date.isoformat(),
        'type': t.transaction_type,
        'desc': t.description or t.category,
        'category': t.category,
        'amount': float(t.amount),
        'method': t.payment_method
    } for t in recent_txns]

    # Active alerts
    alerts = SystemAlert.objects.filter(organization=org, is_read=False)[:5]
    alert_list = [{
        'id': a.alert_id,
        'type': a.alert_type,
        'severity': a.severity,
        'title': a.title,
        'detail': a.detail,
        'date': a.date.isoformat()
    } for a in alerts]

    return {
        'vehicles': {
            'total': total_vehicles,
            'active': active_vehicles,
            'available': status_counts.get('Available', 0),
            'onDuty': status_counts.get('On Duty', 0),
            'inGarage': status_counts.get('In Garage', 0),
            'inactive': status_counts.get('Inactive', 0),
        },
        'owners': {'total': total_owners, 'active': active_owners},
        'drivers': {'total': total_drivers, 'active': active_drivers, 'unassigned': unassigned_drivers},
        'financials': {
            'totalRevenue': float(all_income),
            'totalExpenses': float(all_expense),
            'netProfit': float(all_income - all_expense),
            'customerReceivable': float(total_customer_receivable),
            'ownerPayable': float(total_owner_payable),
            'driverPayable': float(total_driver_payable),
            'garagePayable': float(total_garage_payable),
            'totalCollection': total_collection,
            'totalOwnerPayment': total_owner_payment,
            'cashBalance': float(cash_balance),
            'bankBalance': float(bank_balance),
            'totalBalance': float(cash_balance + bank_balance),
            'comparison': {
                'revenueDiff': rev_diff,
                'revenuePct': rev_pct,
                'curMonthRevenue': float(cur_month_inc),
                'prevMonthRevenue': float(prev_month_inc),
                'curMonthExpense': float(cur_month_exp),
                'prevMonthExpense': float(prev_month_exp),
            }
        },
        'today': {
            'totalDuties': today_duties.count(),
            'running': running_duties,
            'completed': completed_duties,
            'scheduled': scheduled_duties,
            'available': status_counts.get('Available', 0),
            'revenue': float(today_revenue),
            'expenses': float(today_expenses),
        },
        'alertsCount': unread_alerts,
        'alerts': alert_list,
        'monthlyChart': monthly_chart,
        'recentTransactions': recent_list
    }

def generate_system_alerts(org: Organization, target_date=None):
    """Automated engine scanning document expiries, overdue invoices, and maintenance (Module 14)."""
    if not target_date:
        target_date = date.today()

    # 1. Vehicle papers expiring in next 30 days or already expired
    papers = VehiclePaper.objects.filter(organization=org, expiry_date__isnull=False)
    for p in papers:
        days_left = (p.expiry_date - target_date).days
        if days_left <= 30:
            sev = 'danger' if days_left < 0 else ('warning' if days_left <= 7 else 'info')
            t_str = 'Expired' if days_left < 0 else f'Expiring in {days_left}d'
            title = f"{p.get_paper_type_display()} {t_str} – {p.vehicle.reg_no}"
            if not SystemAlert.objects.filter(organization=org, title=title).exists():
                count = SystemAlert.objects.filter(organization=org).count() + 1
                SystemAlert.objects.create(
                    organization=org,
                    alert_id=f'ALT-{count:03d}',
                    alert_type='document',
                    severity=sev,
                    title=title,
                    detail=f"Certificate #{p.paper_number} expired on {p.expiry_date}" if days_left < 0 else f"Certificate #{p.paper_number} expires on {p.expiry_date}",
                    date=target_date,
                    due_date=p.expiry_date,
                    related_id=p.vehicle.reg_no
                )

    # 2. Driver License expiry
    drivers = Party.objects.filter(organization=org, kind=Party.Kind.DRIVER).select_related('detail')
    for d in drivers:
        det = getattr(d, 'detail', None)
        if det and det.license_expiry:
            days_left = (det.license_expiry - target_date).days
            if days_left <= 30:
                sev = 'danger' if days_left < 0 else 'warning'
                title = f"Driver License Expiring Soon – {d.name}"
                if not SystemAlert.objects.filter(organization=org, title=title).exists():
                    count = SystemAlert.objects.filter(organization=org).count() + 1
                    SystemAlert.objects.create(
                        organization=org,
                        alert_id=f'ALT-{count:03d}',
                        alert_type='document',
                        severity=sev,
                        title=title,
                        detail=f"Driver {d.name} license expires on {det.license_expiry}",
                        date=target_date,
                        due_date=det.license_expiry,
                        related_id=f'DRV-{d.pk:03d}'
                    )

    # 3. Client Overdue receivables
    clients = Party.objects.filter(organization=org, kind=Party.Kind.CUSTOMER).select_related('detail')
    for c in clients:
        det = getattr(c, 'detail', None)
        if det and (det.total_receivable_client - det.total_collected_client) > 0:
            due_amt = det.total_receivable_client - det.total_collected_client
            title = f"{c.name} – Outstanding Receivable Due"
            if not SystemAlert.objects.filter(organization=org, title=title).exists():
                count = SystemAlert.objects.filter(organization=org).count() + 1
                SystemAlert.objects.create(
                    organization=org,
                    alert_id=f'ALT-{count:03d}',
                    alert_type='payment',
                    severity='warning' if due_amt < 50000 else 'danger',
                    title=title,
                    detail=f"Receivable balance of ৳{due_amt:,.0f} pending collection",
                    date=target_date,
                    related_id=f'CLT-{c.pk:03d}'
                )

def generate_invoice_number(org: Organization) -> str:
    """Concurrency-safe sequential invoice number generator: INV-YYYY-XXXX."""
    current_year = date.today().year
    prefix = f"INV-{current_year}-"
    last_inv = TasinInvoice.objects.filter(
        organization=org,
        invoice_number__startswith=prefix
    ).order_by('-invoice_number').first()
    
    if last_inv:
        try:
            last_seq = int(last_inv.invoice_number.split('-')[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = TasinInvoice.objects.filter(organization=org).count() + 1
    else:
        new_seq = 1
    return f"{prefix}{new_seq:04d}"

def calculate_vehicle_profitability(org: Organization, start_date=None, end_date=None):
    """
    Module 14: Vehicle Profitability Report
    Net Profit = Duty Income - (Maintenance + Fuel + Mobil + Driver Allowance + Owner Rent + Other Expenses)
    """
    vehicles = Vehicle.objects.filter(organization=org).select_related('detail')
    records = []
    
    total_income = D(0)
    total_maint = D(0)
    total_fuel = D(0)
    total_driver_exp = D(0)
    total_owner_rent = D(0)
    total_net_profit = D(0)
    
    for v in vehicles:
        reg = v.registration
        det = getattr(v, 'detail', None)
        owner_name = v.ownerships.filter(effective_to__isnull=True).values_list('owner__name', flat=True).first() or 'Company Fleet'
        monthly_rent = det.monthly_rent if det else D(0)
        
        # 1. Duty income & driver allowance from completed/running duties
        duties_qs = Duty.objects.filter(organization=org, vehicle=v)
        if start_date:
            duties_qs = duties_qs.filter(date__gte=start_date)
        if end_date:
            duties_qs = duties_qs.filter(date__lte=end_date)
            
        duty_income = duties_qs.aggregate(s=Sum('total_amount'))['s'] or D(0)
        driver_allowance = duties_qs.aggregate(s=Sum('driver_allowance'))['s'] or D(0)
        
        # 2. Maintenance costs
        maint_qs = MaintenanceRecord.objects.filter(organization=org, vehicle_reg=reg)
        if start_date:
            maint_qs = maint_qs.filter(date__gte=start_date)
        if end_date:
            maint_qs = maint_qs.filter(date__lte=end_date)
            
        maintenance_cost = maint_qs.aggregate(s=Sum('total_cost'))['s'] or D(0)
        
        # 3. Fuel & other vehicle expenses from AccountTransaction
        txn_qs = AccountTransaction.objects.filter(organization=org, vehicle_reg=reg, transaction_type='Expense')
        if start_date:
            txn_qs = txn_qs.filter(date__gte=start_date)
        if end_date:
            txn_qs = txn_qs.filter(date__lte=end_date)
            
        fuel_cost = txn_qs.filter(category='Fuel').aggregate(s=Sum('amount'))['s'] or D(0)
        mobil_cost = txn_qs.filter(category__icontains='Mobil').aggregate(s=Sum('amount'))['s'] or D(0)
        other_exp = txn_qs.exclude(category__in=['Fuel', 'Mobil', 'Maintenance']).aggregate(s=Sum('amount'))['s'] or D(0)
        
        total_vehicle_exp = maintenance_cost + fuel_cost + mobil_cost + driver_allowance + monthly_rent + other_exp
        net_profit = duty_income - total_vehicle_exp
        profit_margin = float(round((net_profit / duty_income * 100), 2)) if duty_income > 0 else 0.0
        
        total_income += duty_income
        total_maint += maintenance_cost
        total_fuel += (fuel_cost + mobil_cost)
        total_driver_exp += driver_allowance
        total_owner_rent += monthly_rent
        total_net_profit += net_profit
        
        records.append({
            'vehicleId': str(v.pk),
            'vehicle_id': str(v.pk),
            'regNo': reg,
            'registration': reg,
            'model': f"{det.brand if det else ''} {det.model if det else ''}".strip() or reg,
            'owner': owner_name,
            'owner_name': owner_name,
            'status': det.status if det else 'Available',
            'dutyIncome': float(duty_income),
            'duty_income': float(duty_income),
            'maintenanceCost': float(maintenance_cost),
            'maintenance_cost': float(maintenance_cost),
            'fuelCost': float(fuel_cost + mobil_cost),
            'fuel_mobil_cost': float(fuel_cost + mobil_cost),
            'driverExpense': float(driver_allowance),
            'driver_allowance': float(driver_allowance),
            'ownerRent': float(monthly_rent),
            'owner_rent': float(monthly_rent),
            'otherExpense': float(other_exp),
            'totalExpense': float(total_vehicle_exp),
            'total_expense': float(total_vehicle_exp),
            'netProfit': float(net_profit),
            'net_profit': float(net_profit),
            'profitMargin': profit_margin,
            'margin_pct': profit_margin
        })
        
    summary_dict = {
        'totalDutyIncome': float(total_income),
        'duty_income': float(total_income),
        'totalMaintenance': float(total_maint),
        'maintenance_cost': float(total_maint),
        'totalFuel': float(total_fuel),
        'fuel_mobil_cost': float(total_fuel),
        'totalDriverAllowance': float(total_driver_exp),
        'driver_allowance': float(total_driver_exp),
        'totalOwnerRent': float(total_owner_rent),
        'owner_rent': float(total_owner_rent),
        'total_expense': float(total_maint + total_fuel + total_driver_exp + total_owner_rent),
        'totalNetProfit': float(total_net_profit),
        'net_profit': float(total_net_profit),
        'averageMargin': float(round((total_net_profit / total_income * 100), 2)) if total_income > 0 else 0.0
    }
    return {
        'vehicles': records,
        'summary': summary_dict,
        'totals': summary_dict
    }

def get_document_expiries_report(org: Organization, status_filter=None):
    """
    Module 14: Document Expiry Report
    All vehicle papers & driver licenses with remaining days and urgency status.
    """
    today = date.today()
    items = []
    
    # 1. Vehicle papers
    papers = VehiclePaper.objects.filter(organization=org, expiry_date__isnull=False).select_related('vehicle')
    for p in papers:
        days = (p.expiry_date - today).days
        if days < 0:
            st = 'Expired'
            severity = 'danger'
        elif days <= 7:
            st = 'Expiring in 7 Days'
            severity = 'danger'
        elif days <= 15:
            st = 'Expiring in 15 Days'
            severity = 'warning'
        elif days <= 30:
            st = 'Expiring in 30 Days'
            severity = 'warning'
        else:
            st = 'Valid'
            severity = 'info'
            
        if status_filter == 'expired' and days >= 0:
            continue
        if status_filter == '7d' and not (0 <= days <= 7):
            continue
        if status_filter == '15d' and not (0 <= days <= 15):
            continue
        if status_filter == '30d' and not (0 <= days <= 30):
            continue
            
        st_code = 'expired' if days < 0 else ('urgent_7d' if days <= 7 else ('warning_15d' if days <= 15 else ('notice_30d' if days <= 30 else 'valid')))
        items.append({
            'id': f'DOC-{p.pk}',
            'docType': p.get_paper_type_display(),
            'document_type': p.get_paper_type_display(),
            'entityType': 'Vehicle',
            'entity_type': 'Vehicle',
            'entityName': p.vehicle.registration,
            'entity_name': p.vehicle.registration,
            'identifier': p.vehicle.registration,
            'docNumber': p.paper_number or 'N/A',
            'issueDate': p.issue_date.isoformat() if p.issue_date else '',
            'expiryDate': p.expiry_date.isoformat(),
            'expiry_date': p.expiry_date.isoformat(),
            'daysRemaining': days,
            'days_remaining': days,
            'status': st_code,
            'status_label': st,
            'severity': severity
        })
        
    # 2. Driver licenses
    drivers = Party.objects.filter(organization=org, kind=Party.Kind.DRIVER).select_related('detail')
    for d in drivers:
        det = getattr(d, 'detail', None)
        if det and det.license_expiry:
            days = (det.license_expiry - today).days
            if days < 0:
                st = 'Expired'
                severity = 'danger'
            elif days <= 7:
                st = 'Expiring in 7 Days'
                severity = 'danger'
            elif days <= 15:
                st = 'Expiring in 15 Days'
                severity = 'warning'
            elif days <= 30:
                st = 'Expiring in 30 Days'
                severity = 'warning'
            else:
                st = 'Valid'
                severity = 'info'
                
            if status_filter == 'expired' and days >= 0:
                continue
            if status_filter == '7d' and not (0 <= days <= 7):
                continue
            if status_filter == '15d' and not (0 <= days <= 15):
                continue
            if status_filter == '30d' and not (0 <= days <= 30):
                continue
                
            st_code = 'expired' if days < 0 else ('urgent_7d' if days <= 7 else ('warning_15d' if days <= 15 else ('notice_30d' if days <= 30 else 'valid')))
            items.append({
                'id': f'DRV-LIC-{d.pk}',
                'docType': 'Driving License',
                'document_type': 'Driving License',
                'entityType': 'Driver',
                'entity_type': 'Driver',
                'entityName': d.name,
                'entity_name': d.name,
                'identifier': det.license_no or d.name,
                'docNumber': det.license_no or 'N/A',
                'issueDate': det.joining_date.isoformat() if det.joining_date else '',
                'expiryDate': det.license_expiry.isoformat(),
                'expiry_date': det.license_expiry.isoformat(),
                'daysRemaining': days,
                'days_remaining': days,
                'status': st_code,
                'status_label': st,
                'severity': severity
            })
            
    items.sort(key=lambda x: x['daysRemaining'])
    summary = {
        'expired': sum(1 for x in items if x['days_remaining'] < 0),
        'urgent_7d': sum(1 for x in items if 0 <= x['days_remaining'] <= 7),
        'warning_15d': sum(1 for x in items if 7 < x['days_remaining'] <= 15),
        'notice_30d': sum(1 for x in items if 15 < x['days_remaining'] <= 30),
        'valid': sum(1 for x in items if x['days_remaining'] > 30),
        'total': len(items)
    }
    return {
        'documents': items,
        'summary': summary
    }

def get_account_ledger_report(org: Organization, account_id=None, start_date=None, end_date=None):
    """
    Module 14: Cash & Bank Ledger Report
    Running balance calculation across transactions.
    """
    accounts = FinancialAccount.objects.filter(organization=org, is_active=True)
    if account_id:
        accounts = accounts.filter(pk=account_id)
        
    txns_qs = AccountTransaction.objects.filter(organization=org)
    if start_date:
        txns_qs = txns_qs.filter(date__gte=start_date)
    if end_date:
        txns_qs = txns_qs.filter(date__lte=end_date)
        
    txns = list(txns_qs.order_by('date', 'id'))
    
    total_debit = D(0)  # Income / Receipts
    total_credit = D(0) # Expenses / Payouts
    ledger_rows = []
    
    opening_balance = sum((a.opening_balance for a in accounts), D(0))
    running_balance = opening_balance
    
    for t in txns:
        if t.transaction_type == 'Income':
            debit = t.amount
            credit = D(0)
            running_balance += debit
            total_debit += debit
        elif t.transaction_type == 'Expense':
            debit = D(0)
            credit = t.amount
            running_balance -= credit
            total_credit += credit
        else: # Transfer
            debit = D(0)
            credit = D(0)
            
        ledger_rows.append({
            'id': str(t.pk),
            'transactionId': t.transaction_id,
            'date': t.date.isoformat(),
            'type': t.transaction_type,
            'category': t.category,
            'party': t.related_person or t.vehicle_reg or 'General',
            'description': t.description,
            'method': t.payment_method,
            'reference': t.reference,
            'debit': float(debit),
            'credit': float(credit),
            'runningBalance': float(running_balance),
            'balance': float(running_balance)
        })
        
    first_acc = accounts.first()
    acc_info = {
        'id': first_acc.pk if first_acc else 0,
        'name': first_acc.name if first_acc else 'Combined Accounts',
        'type': first_acc.account_type if first_acc else 'All',
        'bank_name': first_acc.bank_name if first_acc else '',
        'account_number': first_acc.account_number if first_acc else ''
    }

    return {
        'account': acc_info,
        'opening_balance': float(opening_balance),
        'openingBalance': float(opening_balance),
        'closing_balance': float(running_balance),
        'closingBalance': float(running_balance),
        'total_inflow': float(total_debit),
        'totalDebit': float(total_debit),
        'total_outflow': float(total_credit),
        'totalCredit': float(total_credit),
        'transactions': ledger_rows
    }

