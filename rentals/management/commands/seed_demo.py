from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand,CommandError
from rentals.models import Organization,Membership,Party,Vehicle,Agreement,Obligation
from rentals import services

class Command(BaseCommand):
    help='Seed one reproducible September 2026 worked example for an existing user.'
    def add_arguments(self,parser):parser.add_argument('--username',required=True)
    def handle(self,*args,**options):
        user=get_user_model().objects.filter(username=options['username']).first()
        if not user:raise CommandError('Create the user first with createsuperuser')
        org,_=Organization.objects.get_or_create(name='RentFlow Demo')
        Membership.objects.get_or_create(organization=org,user=user,defaults={'role':'admin','branch':services.default_branch(org)})
        def party(kind,name):return Party.objects.get_or_create(organization=org,kind=kind,name=name)[0]
        owner=party('owner','Farhana Rahman');customer=party('customer','Meridian Textiles Ltd.');driver=party('driver','Imran Hossain')
        vehicle,_=Vehicle.objects.get_or_create(organization=org,registration='DHA-METRO-GA-19-7724',defaults={'model_name':'Toyota Axio','owner':owner})
        agreement,_=Agreement.objects.get_or_create(organization=org,vehicle=vehicle,start_date=date(2026,1,1),defaults={'customer':customer,'driver':driver,'customer_rent':Decimal('100000'),'owner_rent':Decimal('60000'),'driver_salary':Decimal('20000'),'owner_offset_allowed':True,'fuel_responsible':'owner','repair_responsible':'owner'})
        period=services.issue_period(org,agreement.pk,date(2026,9,1))
        expense=services.record_expense(org,user,{'period_id':period.pk,'category':'repair','amount':Decimal('8000'),'paid_by':'operator','responsible':'owner','offset_owner':True,'reference':'GAR-0182','description':'Brake assembly repair'},'seed-repair')
        if expense.state=='pending':services.approve_expense(org,user,expense.pk)
        obligations={o.kind:o for o in period.obligations.all()}
        for kind,amount,reference,ob_kind in [('customer_payment','40000','BRAC-24590','customer_rent'),('customer_payment','30000','BRAC-25918','customer_rent'),('owner_payout','40000','BEFTN-8712','owner_rent'),('driver_payout','15000','BKS-18723','driver_salary')]:
            services.record_payment(org,user,{'kind':kind,'amount':Decimal(amount),'reference':reference,'method':'bank','event_date':date(2026,9,20),'allocations':[{'obligation_id':obligations[ob_kind].pk,'amount':Decimal(amount)}]},'seed-'+reference)
        self.stdout.write(self.style.SUCCESS(f'Organization ID {org.pk}, agreement ID {agreement.pk}, period ID {period.pk}'))
        self.stdout.write(str(services.statement(period)))
