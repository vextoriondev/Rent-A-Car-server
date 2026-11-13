from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rentals.models import (
    Organization, Party, Vehicle, Duty, Garage, MaintenanceRecord,
    VehiclePaper, FinancialAccount, AccountTransaction, SystemAlert,
    TasinInvoice, TasinInvoiceItem, TasinInvoicePayment
)

class Command(BaseCommand):
    help = 'Safely deletes only synthetic test records created by seed_tasin_demo.'

    def add_arguments(self, parser):
        parser.add_argument('--org-name', default='TASIN ENTERPRISE (TEST)',
                            help='Organization name of demo records to clean up.')
        parser.add_argument('--confirm', action='store_true',
                            help='Confirm deletion of synthetic test organization and its records.')

    def handle(self, *args, **options):
        org_name = options['org_name']
        if not options['confirm']:
            raise CommandError(
                f"SAFETY CHECK: Must pass --confirm to delete records for organization '{org_name}'."
            )

        org = Organization.objects.filter(name=org_name).first()
        if not org:
            self.stdout.write(self.style.WARNING(f"No organization found with name '{org_name}'. Nothing to clean up."))
            return

        with transaction.atomic():
            inv_p = TasinInvoicePayment.objects.filter(invoice__organization=org).delete()[0]
            inv_i = TasinInvoiceItem.objects.filter(invoice__organization=org).delete()[0]
            invs = TasinInvoice.objects.filter(organization=org).delete()[0]
            duties = Duty.objects.filter(organization=org).delete()[0]
            mnt = MaintenanceRecord.objects.filter(organization=org).delete()[0]
            txns = AccountTransaction.objects.filter(organization=org).delete()[0]
            papers = VehiclePaper.objects.filter(organization=org).delete()[0]
            vehs = Vehicle.objects.filter(organization=org).delete()[0]
            parties = Party.objects.filter(organization=org).delete()[0]
            garages = Garage.objects.filter(organization=org).delete()[0]
            accs = FinancialAccount.objects.filter(organization=org).delete()[0]
            alerts = SystemAlert.objects.filter(organization=org).delete()[0]
            org.delete()

        self.stdout.write(self.style.SUCCESS(
            f"Successfully cleaned up demo organization '{org_name}' and its records:\n"
            f"  - Invoices & Items: {invs + inv_i + inv_p}\n"
            f"  - Duties: {duties}\n"
            f"  - Maintenance: {mnt}\n"
            f"  - Transactions: {txns}\n"
            f"  - Vehicles & Papers: {vehs + papers}\n"
            f"  - Parties & Garages: {parties + garages}\n"
            f"  - Accounts: {accs}\n"
            f"  - Alerts: {alerts}"
        ))
