from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from ..models import Organization, Membership, Party, Vehicle, Agreement
from .. import services

def build_worked_fixture():
    """Creates the worked September 2026 fixture: one agreement, one issued period, three obligations."""
    user=get_user_model().objects.create_user('accountant',password='test-only-password')
    org=Organization.objects.create(name='Sample Transport')
    Membership.objects.create(organization=org,user=user,role='accountant')
    owner=Party.objects.create(organization=org,kind='owner',name='Farhana Rahman')
    driver=Party.objects.create(organization=org,kind='driver',name='Imran Hossain')
    customer=Party.objects.create(organization=org,kind='customer',name='Meridian Textiles Ltd.')
    car=Vehicle.objects.create(organization=org,registration='DHA-19-7724',model_name='Toyota Axio',owner=owner)
    agreement=Agreement.objects.create(organization=org,vehicle=car,customer=customer,driver=driver,start_date=date(2026,1,1),customer_rent=Decimal('100000'),owner_rent=Decimal('60000'),driver_salary=Decimal('20000'),repair_responsible='owner',fuel_responsible='owner',owner_offset_allowed=True)
    period=services.issue_period(org,agreement.pk,date(2026,9,1))
    obligations={x.kind:x for x in period.obligations.all()}
    return {'user':user,'org':org,'owner':owner,'driver':driver,'customer':customer,'car':car,'agreement':agreement,'period':period,'ob':obligations}

class RentalsTestCase(TestCase):
    """Worked September 2026 fixture shared by the ledger flow tests."""
    def setUp(self):
        fixture=build_worked_fixture()
        self.user=fixture['user']
        self.org=fixture['org']
        self.owner=fixture['owner']
        self.driver=fixture['driver']
        self.customer=fixture['customer']
        self.car=fixture['car']
        self.agreement=fixture['agreement']
        self.period=fixture['period']
        self.ob=fixture['ob']

    def pay(self,kind,amount,reference):
        target={'customer_payment':'customer_rent','owner_payout':'owner_rent','driver_payout':'driver_salary'}[kind]
        return services.record_payment(self.org,self.user,{'kind':kind,'amount':Decimal(amount),'reference':reference,'method':'bank','event_date':date(2026,9,20),'allocations':[{'obligation_id':self.ob[target].pk,'amount':Decimal(amount)}]},reference)
