from datetime import date
from decimal import Decimal
from ..models import Agreement
from .. import services
from .base import RentalsTestCase

class AgreementOverlapTests(RentalsTestCase):
    """Gap #3 closed in M2: the domain layer (validate_agreement / create_agreement) rejects an overlapping
    active agreement on the same vehicle, whichever path creates it."""
    def make(self,**kw):
        return Agreement(organization=self.org,vehicle=self.car,customer=self.customer,driver=self.driver,start_date=kw.get('start',date(2026,1,1)),end_date=kw.get('end'),
            customer_rent=Decimal('50000'),owner_rent=Decimal('30000'),driver_salary=Decimal('10000'))

    def test_domain_layer_rejects_overlapping_vehicle_agreements(self):
        with self.assertRaises(services.DomainError):services.validate_agreement(self.make())
        with self.assertRaises(services.DomainError):services.create_agreement(self.org,self.user,self.make())
        self.assertEqual(Agreement.objects.filter(vehicle=self.car).count(),1)

    def test_non_overlapping_agreement_is_allowed(self):
        self.agreement.end_date=date(2026,12,31);self.agreement.save()
        a,_=services.create_agreement(self.org,self.user,self.make(start=date(2027,1,1)))
        self.assertEqual(a.versions.get().number,1)

    def test_driver_overlap_only_warns(self):
        from ..models import Vehicle,Party
        car2=Vehicle.objects.create(organization=self.org,registration='DHA-2',model_name='Honda',owner=self.owner)
        a=Agreement(organization=self.org,vehicle=car2,customer=self.customer,driver=self.driver,start_date=date(2026,1,1),customer_rent=Decimal('1'),owner_rent=Decimal('1'),driver_salary=Decimal('1'))
        a,warnings=services.create_agreement(self.org,self.user,a)
        self.assertEqual(len(warnings),1);self.assertTrue(a.active)

    def test_quick_endpoint_cannot_bypass_overlap(self):
        from django.test import Client
        c=Client();c.force_login(self.user)
        body={'customer':'C','owner':'O','driver':'D','vehicle':'X','registration':'NEW-1','start':'2026-01-01','rent':'10','owner_rent':'5','salary':'2'}
        self.assertEqual(c.post(f'/api/v1/orgs/{self.org.pk}/agreements/quick',body,content_type='application/json').status_code,200)
        body2={'agreement_id':0,'vehicle_id':self.car.pk,'customer_id':self.customer.pk,'driver_id':self.driver.pk,'start_date':'2026-02-01','customer_rent':'1','owner_rent':'1','driver_salary':'1'}
        self.assertEqual(c.post(f'/api/v1/orgs/{self.org.pk}/agreements',body2,content_type='application/json').status_code,409)

    def test_quick_endpoint_stores_the_driver_arrangement(self):
        from django.test import Client
        c=Client();c.force_login(self.user)
        body={'customer':'C','owner':'O','driver':'D','vehicle':'X','registration':'NEW-2','start':'2026-01-01','rent':'10','owner_rent':'5','salary':'0','driver_provider':'customer','driver_paid_by':'customer','clauses':'Customer pays the driver directly'}
        r=c.post(f'/api/v1/orgs/{self.org.pk}/agreements/quick',body,content_type='application/json');self.assertEqual(r.status_code,200,r.content)
        a=Agreement.objects.get(pk=r.json()['id']);self.assertEqual((a.driver_provider,a.driver_paid_by,a.clauses),('customer','customer','Customer pays the driver directly'))
        body['registration']='NEW-3';body['driver_paid_by']='operator'
        self.assertEqual(c.post(f'/api/v1/orgs/{self.org.pk}/agreements/quick',body,content_type='application/json').status_code,422)   # operator pays but salary is 0

    def test_agreement_starting_mid_month_can_issue_its_first_month(self):
        from ..models import Vehicle
        a=self.make(start=date(2026,9,23));a.vehicle=Vehicle.objects.create(organization=self.org,registration='MID-1',model_name='Mid',owner=self.car.owner)
        a,_=services.create_agreement(self.org,self.user,a)
        p=services.issue_period(self.org,a.pk,date(2026,9,1))
        self.assertEqual(p.agreement_version.number,1)
