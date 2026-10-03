from decimal import Decimal
from django.db import migrations

CHART=[('CASH','Cash and bank','asset',''),('CUSTOMER_RECEIVABLE','Customer receivable','asset','customer'),('RENT_REVENUE','Rent revenue','revenue',''),('OTHER_REVENUE','Other revenue','revenue',''),
    ('OWNER_RENT_EXPENSE','Owner rent expense','expense',''),('OWNER_PAYABLE','Owner payable','liability','owner'),('DRIVER_SALARY_EXPENSE','Driver salary expense','expense',''),('DRIVER_PAYABLE','Driver payable','liability','driver'),
    ('OPERATING_EXPENSE','Operating expense','expense',''),('OWNER_RECOVERABLE','Owner recoverable','asset','owner'),('CUSTOMER_RECOVERABLE','Customer recoverable','asset','customer'),('OPENING_BALANCE','Opening balance equity','equity','')]
ZERO=Decimal('0.00')

def backfill(apps,schema_editor):
    """Give every historical journal line its account FK and party/agreement/period/vehicle dimensions.
    Payment entries are rewritten as one pair of lines per allocation (same totals) so each line has a single period;
    salary-advance payments are retagged source_type=payment_advance. Not reversible with data (reverse is a no-op)."""
    Org=apps.get_model('rentals','Organization');Account=apps.get_model('rentals','Account');Entry=apps.get_model('rentals','JournalEntry');Line=apps.get_model('rentals','JournalLine')
    Obligation=apps.get_model('rentals','Obligation');Payment=apps.get_model('rentals','Payment');Allocation=apps.get_model('rentals','Allocation');Expense=apps.get_model('rentals','Expense')
    for org in Org.objects.all():
        accounts={code:Account.objects.get_or_create(organization=org,code=code,defaults={'name':name,'type':kind,'subledger_kind':sub})[0] for code,name,kind,sub in CHART}
        def dims(period):return {'period':period,'agreement_id':period.agreement_id,'vehicle_id':period.agreement.vehicle_id}
        for entry in Entry.objects.filter(organization=org).order_by('pk'):
            lines=list(entry.lines.all())
            if entry.source_type=='obligation':
                ob=Obligation.objects.select_related('period__agreement','party').get(pk=entry.source_id)
                for l in lines:
                    l.account_ref=accounts[l.account];l.party=ob.party if l.account in('CUSTOMER_RECEIVABLE','OWNER_PAYABLE','DRIVER_PAYABLE') else None
                    for k,v in dims(ob.period).items():setattr(l,k,v)
                    l.save()
            elif entry.source_type=='payment':
                pay=Payment.objects.select_related('party').get(pk=entry.source_id)
                allocs=list(Allocation.objects.select_related('obligation__period__agreement').filter(payment=pay).order_by('pk'))
                entry.lines.all().delete()
                for al in allocs:
                    d=dims(al.obligation.period);amt=al.amount
                    if pay.kind=='customer_payment':rows=[('CASH',amt,ZERO,None),('CUSTOMER_RECEIVABLE',ZERO,amt,pay.party)]
                    elif pay.kind=='owner_payout':rows=[('OWNER_PAYABLE',amt,ZERO,pay.party),('CASH',ZERO,amt,None)]
                    else:rows=[('DRIVER_PAYABLE',amt,ZERO,pay.party),('CASH',ZERO,amt,None)]
                    for code,dr,cr,party in rows:Line.objects.create(entry=entry,account=code,account_ref=accounts[code],debit=dr,credit=cr,party=party,**d)
                if pay.kind=='salary_advance':entry.source_type='payment_advance';entry.save(update_fields=['source_type'])
            elif entry.source_type in('expense','expense_reversal'):
                exp=Expense.objects.select_related('period__agreement').get(pk=entry.source_id)
                owner=Obligation.objects.filter(period=exp.period,kind='owner_rent').select_related('party').first()
                for l in lines:
                    l.account_ref=accounts[l.account]
                    l.party=owner.party if (l.account in('OWNER_PAYABLE','OWNER_RECOVERABLE') and owner) else (exp.period.agreement.customer if l.account=='CUSTOMER_RECOVERABLE' else None)
                    for k,v in dims(exp.period).items():setattr(l,k,v)
                    l.save()
            else:
                for l in lines:l.account_ref=accounts.get(l.account);l.save()

class Migration(migrations.Migration):
    dependencies=[('rentals','0010_ledger_accounts_charges_invoices')]
    operations=[migrations.RunPython(backfill,migrations.RunPython.noop)]
