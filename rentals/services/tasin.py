from decimal import Decimal
from datetime import date, datetime, timedelta
from django.db import transaction
from django.db.models import Sum, Count, Q
from ..models import (
    Organization, Party, Vehicle,
    VehicleDetail, PartyDetail, Duty, Garage, MaintenanceRecord,
    VehiclePaper, FinancialAccount, AccountTransaction, SystemAlert, CompanyProfile
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
