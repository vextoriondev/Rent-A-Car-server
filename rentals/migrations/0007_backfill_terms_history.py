from django.db import migrations

def backfill(apps,schema_editor):
    Agreement=apps.get_model('rentals','Agreement');Version=apps.get_model('rentals','AgreementVersion')
    Ownership=apps.get_model('rentals','VehicleOwnership');Assignment=apps.get_model('rentals','DriverAssignment');Period=apps.get_model('rentals','Period')
    for a in Agreement.objects.select_related('vehicle').order_by('start_date','pk'):
        v,_=Version.objects.get_or_create(agreement=a,number=1,defaults={'effective_from':a.start_date,'customer_rent':a.customer_rent,'owner_rent':a.owner_rent,'driver_salary':a.driver_salary,'fuel_responsible':a.fuel_responsible,'repair_responsible':a.repair_responsible,'owner_offset_allowed':a.owner_offset_allowed,'status':'active','reason':'Initial terms (migrated)'})
        if not Ownership.objects.filter(vehicle_id=a.vehicle_id).exists():Ownership.objects.create(organization_id=a.organization_id,vehicle_id=a.vehicle_id,owner_id=a.vehicle.owner_id,effective_from=a.start_date)
        if not Assignment.objects.filter(agreement=a).exists():Assignment.objects.create(organization_id=a.organization_id,agreement=a,driver_id=a.driver_id,effective_from=a.start_date)
        Period.objects.filter(agreement=a,agreement_version__isnull=True).update(agreement_version=v)
        a.status='active' if a.active else 'ended';a.save(update_fields=['status'])

class Migration(migrations.Migration):
    dependencies=[('rentals','0006_agreement_terms_history')]
    operations=[migrations.RunPython(backfill,migrations.RunPython.noop)]
