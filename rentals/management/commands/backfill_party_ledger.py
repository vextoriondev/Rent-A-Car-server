from django.core.management.base import BaseCommand
from rentals.services.finance import backfill_legacy_counters


class Command(BaseCommand):
    help = 'Mirror legacy PartyDetail balance counters into the party ledger (idempotent, non-destructive).'

    def handle(self, *args, **options):
        self.stdout.write(f'{backfill_legacy_counters()} ledger row(s) created')
