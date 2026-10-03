from django.db import migrations

def backfill(apps,schema_editor):
    """Every already-closed period gets a closed Settlement (no frozen lines: they were never recorded) so it can be reopened later."""
    Period=apps.get_model('rentals','Period');Settlement=apps.get_model('rentals','Settlement')
    for p in Period.objects.filter(closed_at__isnull=False):
        Settlement.objects.get_or_create(period=p,defaults={'organization_id':p.organization_id,'agreement_id':p.agreement_id,'month':p.month,'state':'closed','closed_by_id':p.closed_by_id,'closed_at':p.closed_at})

class Migration(migrations.Migration):
    dependencies=[('rentals','0012_settlement_engine')]
    operations=[migrations.RunPython(backfill,migrations.RunPython.noop)]
