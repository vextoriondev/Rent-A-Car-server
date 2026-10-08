from django.db import migrations

def backfill(apps,schema_editor):
    """Every existing vehicle and agreement goes to its organization's default 'Main' branch (an agreement takes its vehicle's branch)."""
    Organization=apps.get_model('rentals','Organization');Branch=apps.get_model('rentals','Branch')
    Vehicle=apps.get_model('rentals','Vehicle');Agreement=apps.get_model('rentals','Agreement')
    for org in Organization.objects.all():
        main=Branch.objects.get_or_create(organization=org,name='Main')[0]
        Vehicle.objects.filter(organization=org,branch__isnull=True).update(branch=main)
        for vehicle in Vehicle.objects.filter(organization=org):Agreement.objects.filter(vehicle=vehicle,branch__isnull=True).update(branch_id=vehicle.branch_id)

class Migration(migrations.Migration):
    dependencies=[('rentals','0016_branches_in_data')]
    operations=[migrations.RunPython(backfill,migrations.RunPython.noop)]
