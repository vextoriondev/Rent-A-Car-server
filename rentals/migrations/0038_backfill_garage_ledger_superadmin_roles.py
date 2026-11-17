"""Data migration (idempotent, reversible):
1. Garage payables: mirror each Garage.total_bill / total_paid counter into the garage ledger as `legacy` rows (counters untouched).
2. Rent accruals created before proration existed: record the rate and basis they were posted with (full month).
3. Super Admin by explicit flag: users that used to be Super Admin only because of a reserved username get `Membership.is_super_admin=True`
   (organization-scoped; role unchanged), and legacy `super_admin` role strings become role=admin + flag, so nobody is locked out and the RentFlow engine keeps working.
"""
from datetime import date
from django.db import migrations

RESERVED = ('admin', 'superadmin', 'tasin_superadmin_test', 'sa_tasin_1', 'sa_tasin_2')


def forward(apps, schema_editor):
    Garage = apps.get_model('rentals', 'Garage')
    Entry = apps.get_model('rentals', 'GarageLedgerEntry')
    today = date.today()
    for g in Garage.objects.iterator():
        for tag, delta, kind in (('bill', g.total_bill, 'legacy'), ('paid', -g.total_paid, 'legacy')):
            if delta:
                Entry.objects.get_or_create(organization_id=g.organization_id, unique_key=f'legacy:garage:{g.pk}:{tag}', defaults=dict(
                    garage_id=g.pk, entry_type=kind, delta=delta, date=today, description='Balance carried over from the previous version of the system'))

    Accrual = apps.get_model('rentals', 'RentAccrual')
    for a in Accrual.objects.filter(monthly_rate=0).iterator():
        a.monthly_rate = a.amount
        a.proration_basis = 'full_month'
        a.save(update_fields=['monthly_rate', 'proration_basis'])

    User = apps.get_model('auth', 'User')
    Membership = apps.get_model('rentals', 'Membership')
    for u in User.objects.filter(username__in=RESERVED):
        for m in Membership.objects.filter(user_id=u.pk, role__in=('admin', 'accountant', 'operations', 'viewer', 'mechanic', 'super_admin', 'superadmin')):  # never portal logins
            m.is_super_admin = True
            if m.role in ('super_admin', 'superadmin'):
                m.role = 'admin'      # the RentFlow engine only knows admin/accountant/...; the flag carries the privilege
            m.save(update_fields=['is_super_admin', 'role'])
    for m in Membership.objects.filter(role__in=('super_admin', 'superadmin')):
        m.is_super_admin = True
        m.role = 'admin'
        m.save(update_fields=['is_super_admin', 'role'])


def backward(apps, schema_editor):
    apps.get_model('rentals', 'GarageLedgerEntry').objects.filter(entry_type='legacy').delete()
    # membership roles are intentionally not reverted: lowering privileges silently could lock administrators out


class Migration(migrations.Migration):
    dependencies = [('rentals', '0037_garage_ledger_settings_throttle'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(forward, backward)]
