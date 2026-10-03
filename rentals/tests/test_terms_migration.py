from datetime import date
from decimal import Decimal
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

class BackfillTermsMigrationTests(TransactionTestCase):
    """0007 gives pre-M2 agreements/periods a v1 version, ownership and driver assignment without changing any amount."""
    def migrate(self,target):
        ex=MigrationExecutor(connection);ex.migrate([('rentals',target)]);return ex.loader.project_state([('rentals',target)]).apps

    def tearDown(self):
        ex=MigrationExecutor(connection);ex.migrate(ex.loader.graph.leaf_nodes())

    def test_backfill_from_legacy_rows(self):
        apps=self.migrate('0005_default_branches')
        Org=apps.get_model('rentals','Organization');Party=apps.get_model('rentals','Party');Vehicle=apps.get_model('rentals','Vehicle')
        Agreement=apps.get_model('rentals','Agreement') ;Period=apps.get_model('rentals','Period')
        org=Org.objects.create(name='Legacy');o=Party.objects.create(organization=org,kind='owner',name='O');c=Party.objects.create(organization=org,kind='customer',name='C');d=Party.objects.create(organization=org,kind='driver',name='D')
        car=Vehicle.objects.create(organization=org,registration='L-1',model_name='M',owner=o)
        a=Agreement.objects.create(organization=org,vehicle=car,customer=c,driver=d,start_date=date(2026,1,1),customer_rent=Decimal('100000'),owner_rent=Decimal('60000'),driver_salary=Decimal('20000'),owner_offset_allowed=True)
        Period.objects.create(organization=org,agreement=a,month=date(2026,9,1))
        ended=Agreement.objects.create(organization=org,vehicle=car,customer=c,driver=d,start_date=date(2025,1,1),end_date=date(2025,12,31),customer_rent=Decimal('1'),owner_rent=Decimal('1'),driver_salary=Decimal('1'),active=False)
        apps=self.migrate('0007_backfill_terms_history')
        V=apps.get_model('rentals','AgreementVersion');P=apps.get_model('rentals','Period');A=apps.get_model('rentals','Agreement')
        v=V.objects.get(agreement_id=a.pk);self.assertEqual((v.number,v.effective_from,v.customer_rent,v.owner_rent,v.driver_salary,v.owner_offset_allowed,v.status),(1,date(2026,1,1),Decimal('100000'),Decimal('60000'),Decimal('20000'),True,'active'))
        self.assertEqual(P.objects.get(agreement_id=a.pk).agreement_version_id,v.pk)
        self.assertEqual(apps.get_model('rentals','VehicleOwnership').objects.filter(vehicle_id=car.pk).count(),1)   # one per vehicle, not per agreement
        self.assertEqual(apps.get_model('rentals','DriverAssignment').objects.filter(agreement_id=a.pk).count(),1)
        self.assertEqual((A.objects.get(pk=a.pk).status,A.objects.get(pk=ended.pk).status),('active','ended'))
