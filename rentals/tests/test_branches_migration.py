from datetime import date
from decimal import Decimal
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

class BackfillBranchesMigrationTests(TransactionTestCase):
    """0017 puts every pre-M14 vehicle and agreement in its organization's default 'Main' branch, per organization."""
    def migrate(self,target):
        ex=MigrationExecutor(connection);ex.migrate([('rentals',target)]);return ex.loader.project_state([('rentals',target)]).apps

    def tearDown(self):
        ex=MigrationExecutor(connection);ex.migrate(ex.loader.graph.leaf_nodes())

    def test_backfill_assigns_main_per_organization(self):
        apps=self.migrate('0016_branches_in_data')
        Org=apps.get_model('rentals','Organization');Party=apps.get_model('rentals','Party');Vehicle=apps.get_model('rentals','Vehicle');Agreement=apps.get_model('rentals','Agreement')
        made={}
        for name in('A','B'):
            org=Org.objects.create(name=name);o=Party.objects.create(organization=org,kind='owner',name='O');c=Party.objects.create(organization=org,kind='customer',name='C');d=Party.objects.create(organization=org,kind='driver',name='D')
            car=Vehicle.objects.create(organization=org,registration=name+'-1',model_name='M',owner=o)
            a=Agreement.objects.create(organization=org,vehicle=car,customer=c,driver=d,start_date=date(2026,1,1),customer_rent=Decimal('1'),owner_rent=Decimal('1'),driver_salary=Decimal('1'))
            made[name]=(org,car,a)
        apps=self.migrate('0017_backfill_branches')
        Branch=apps.get_model('rentals','Branch');V=apps.get_model('rentals','Vehicle');A=apps.get_model('rentals','Agreement')
        for name,(org,car,a) in made.items():
            main=Branch.objects.get(organization_id=org.pk,name='Main')
            self.assertEqual(V.objects.get(pk=car.pk).branch_id,main.pk);self.assertEqual(A.objects.get(pk=a.pk).branch_id,main.pk)
        self.assertNotEqual(V.objects.get(pk=made['A'][1].pk).branch_id,V.objects.get(pk=made['B'][1].pk).branch_id)
