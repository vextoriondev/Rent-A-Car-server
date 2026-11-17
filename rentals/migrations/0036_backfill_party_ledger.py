"""Copy the legacy PartyDetail counters into the party ledger so the ledger is complete from day one.
Idempotent (unique_key) and non-destructive: the legacy counters are left untouched. Reverse removes only the `legacy` rows."""
from datetime import date
from decimal import Decimal
from django.db import migrations

Z = Decimal('0')


def forward(apps, schema_editor):
    PartyDetail = apps.get_model('rentals', 'PartyDetail')
    Entry = apps.get_model('rentals', 'PartyLedgerEntry')
    today = date.today()

    def post(det, account, delta, tag):
        if not delta:
            return
        Entry.objects.get_or_create(
            organization_id=det.party.organization_id, unique_key=f'legacy:{det.party_id}:{account}:{tag}',
            defaults=dict(party_id=det.party_id, account=account, entry_type='legacy', delta=delta, date=today,
                          description='Balance carried over from the previous version of the system', source_type='migration'))

    for det in PartyDetail.objects.select_related('party').iterator():
        kind = det.party.kind
        if kind == 'owner':
            post(det, 'owner_payable', det.total_payable_owner, 'credit')
            post(det, 'owner_payable', -det.total_paid_owner, 'debit')
        elif kind == 'customer':
            post(det, 'client_receivable', det.total_receivable_client, 'credit')
            post(det, 'client_receivable', -det.total_collected_client, 'debit')
        elif kind == 'driver':
            post(det, 'driver_advance', det.advance, 'advance')
            post(det, 'driver_fuel_gas', (det.fuel_advance or Z) + (det.gas_advance or Z), 'fuelgas')
            post(det, 'driver_salary', det.total_paid_driver, 'paid-credit')
            post(det, 'driver_salary', -det.total_paid_driver, 'paid-debit')


def link_maintenance(apps, schema_editor):
    """Maintenance rows created through the API only stored the registration text, so vehicle history missed them."""
    M = apps.get_model('rentals', 'MaintenanceRecord')
    V = apps.get_model('rentals', 'Vehicle')
    for m in M.objects.filter(vehicle__isnull=True).iterator():
        v = V.objects.filter(organization_id=m.organization_id, registration=m.vehicle_reg).first()
        if v:
            m.vehicle_id = v.pk
            m.save(update_fields=['vehicle'])


def backward(apps, schema_editor):
    apps.get_model('rentals', 'PartyLedgerEntry').objects.filter(entry_type='legacy').delete()


class Migration(migrations.Migration):
    dependencies = [('rentals', '0035_finance_ledger_and_workflows')]
    operations = [migrations.RunPython(forward, backward), migrations.RunPython(link_maintenance, migrations.RunPython.noop)]
