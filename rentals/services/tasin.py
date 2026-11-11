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
            'cashBalance': float(cash_balance),
            'bankBalance': float(bank_balance),
            'totalBalance': float(cash_balance + bank_balance),
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
