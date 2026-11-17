from django.core.management.base import BaseCommand
from rentals.services.finance import reconcile_ledger
from rentals.services.garage import reconcile_garages


class Command(BaseCommand):
    help = 'Read-only: recompute party and garage balances from the ledgers and compare with the legacy caches. Exit status 1 when inconsistent.'

    def handle(self, *args, **options):
        problems = reconcile_ledger() + reconcile_garages()
        for p in problems:
            self.stdout.write(self.style.ERROR(p))
        self.stdout.write(self.style.SUCCESS('Ledger consistent') if not problems else f'{len(problems)} discrepancy(ies)')
        if problems:
            raise SystemExit(1)
