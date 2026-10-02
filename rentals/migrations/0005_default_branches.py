from django.db import migrations

def add_default_branches(apps,schema_editor):
    Organization=apps.get_model('rentals','Organization');Branch=apps.get_model('rentals','Branch')
    for org in Organization.objects.all():Branch.objects.get_or_create(organization=org,name='Main')

class Migration(migrations.Migration):
    dependencies=[('rentals','0004_identities_branches_consent')]
    operations=[migrations.RunPython(add_default_branches,migrations.RunPython.noop)]
