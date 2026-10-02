from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db.models import Sum
from rentals.models import JournalEntry, JournalLine, Organization, Period
from rentals import services

class Command(BaseCommand):
    help='Verify every journal entry balances and every period statement reconciles (ledger vs obligations). Exits non-zero on any problem.'
    def add_arguments(self,parser):parser.add_argument('--org',type=int,help='Limit to one organization id')
    def handle(self,*args,**options):
        problems=[]
        orgs=Organization.objects.all()
        if options['org']:orgs=orgs.filter(pk=options['org'])
        for org in orgs:
            for entry in JournalEntry.objects.filter(organization=org):
                t=entry.lines.aggregate(d=Sum('debit'),c=Sum('credit'))
                if (t['d'] or 0)!=(t['c'] or 0):problems.append(f'org {org.pk}: entry {entry.pk} ({entry.source_type}:{entry.source_id}) is unbalanced')
            for line in JournalLine.objects.filter(entry__organization=org,account_ref__isnull=True):problems.append(f'org {org.pk}: line {line.pk} has no account')
            for period in Period.objects.filter(organization=org).select_related('agreement'):
                diff=services.diff_statements(services.ledger_statement(period),services.obligation_statement(period))
                if diff:problems.append(f'org {org.pk}: period {period.pk} ledger and obligations disagree on {diff}')
        for p in problems:self.stderr.write(p)
        if problems:raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('Ledger OK: all entries balance and all period statements reconcile.'))
