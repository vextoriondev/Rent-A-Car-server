from datetime import date
from decimal import Decimal
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

D=Decimal

class BackfillLedgerMigrationTests(TransactionTestCase):
    """0011 gives pre-M4 journal lines an account FK and period/party/agreement/vehicle dimensions without changing totals."""
    def migrate(self,target):
        ex=MigrationExecutor(connection);ex.migrate([('rentals',target)]);return ex.loader.project_state([('rentals',target)]).apps

    def tearDown(self):
        self.migrate('0011_backfill_ledger_dimensions')

    def test_backfill_from_legacy_journal(self):
        apps=self.migrate('0010_ledger_accounts_charges_invoices')
        g=apps.get_model;User=g('auth','User')
        Org=g('rentals','Organization');Party=g('rentals','Party');Vehicle=g('rentals','Vehicle');Agreement=g('rentals','Agreement');Period=g('rentals','Period');Ob=g('rentals','Obligation')
        Payment=g('rentals','Payment');Alloc=g('rentals','Allocation');Entry=g('rentals','JournalEntry');Line=g('rentals','JournalLine');Expense=g('rentals','Expense')
        user=User.objects.create(username='legacy');org=Org.objects.create(name='Legacy')
        o=Party.objects.create(organization=org,kind='owner',name='O');c=Party.objects.create(organization=org,kind='customer',name='C');d=Party.objects.create(organization=org,kind='driver',name='D')
        car=Vehicle.objects.create(organization=org,registration='L-9',model_name='M',owner=o)
        a=Agreement.objects.create(organization=org,vehicle=car,customer=c,driver=d,start_date=date(2026,1,1),customer_rent=D(100),owner_rent=D(60),driver_salary=D(20))
        periods=[Period.objects.create(organization=org,agreement=a,month=date(2026,m,1)) for m in (8,9)]
        cust=[];drv=[]
        for p in periods:
            for kind,party,amount,lines in (('customer_rent',c,100,[('CUSTOMER_RECEIVABLE',100,0),('RENT_REVENUE',0,100)]),('owner_rent',o,60,[('OWNER_RENT_EXPENSE',60,0),('OWNER_PAYABLE',0,60)]),('driver_salary',d,20,[('DRIVER_SALARY_EXPENSE',20,0),('DRIVER_PAYABLE',0,20)])):
                ob=Ob.objects.create(period=p,kind=kind,party=party,amount=D(amount));e=Entry.objects.create(organization=org,source_type='obligation',source_id=str(ob.pk))
                for acct,dr,cr in lines:Line.objects.create(entry=e,account=acct,debit=D(dr),credit=D(cr))
                if kind=='customer_rent':cust.append(ob)
                if kind=='driver_salary':drv.append(ob)
        pay=Payment.objects.create(organization=org,kind='customer_payment',party=c,amount=D(150),reference='P1',event_date=date(2026,9,20),recorded_by=user)
        Alloc.objects.create(payment=pay,obligation=cust[0],amount=D(100));Alloc.objects.create(payment=pay,obligation=cust[1],amount=D(50))
        e=Entry.objects.create(organization=org,source_type='payment',source_id=str(pay.pk))
        Line.objects.create(entry=e,account='CASH',debit=D(150),credit=D(0));Line.objects.create(entry=e,account='CUSTOMER_RECEIVABLE',debit=D(0),credit=D(150))
        adv=Payment.objects.create(organization=org,kind='salary_advance',party=d,amount=D(5),reference='A1',event_date=date(2026,9,2),recorded_by=user)
        Alloc.objects.create(payment=adv,obligation=drv[1],amount=D(5))
        e=Entry.objects.create(organization=org,source_type='payment',source_id=str(adv.pk))
        Line.objects.create(entry=e,account='DRIVER_PAYABLE',debit=D(5),credit=D(0));Line.objects.create(entry=e,account='CASH',debit=D(0),credit=D(5))
        exp=Expense.objects.create(period=periods[1],category='repair',amount=D(8),paid_by='operator',responsible='owner',offset_owner=True,reference='X',recorded_by=user,state='posted')
        e=Entry.objects.create(organization=org,source_type='expense',source_id=str(exp.pk))
        for acct,dr,cr in (('OWNER_RECOVERABLE',8,0),('CASH',0,8),('OWNER_PAYABLE',8,0),('OWNER_RECOVERABLE',0,8)):Line.objects.create(entry=e,account=acct,debit=D(dr),credit=D(cr))
        before=sum(l.debit-l.credit for l in Line.objects.filter(account='CASH'))

        apps=self.migrate('0011_backfill_ledger_dimensions');g=apps.get_model
        Line=g('rentals','JournalLine');Entry=g('rentals','JournalEntry');Account=g('rentals','Account')
        self.assertEqual(Account.objects.filter(organization_id=org.pk).count(),12)
        self.assertFalse(Line.objects.filter(entry__organization_id=org.pk,account_ref__isnull=True).exists())
        self.assertFalse(Line.objects.filter(entry__organization_id=org.pk,period__isnull=True).exists())
        pay_lines=Line.objects.filter(entry__source_type='payment',entry__source_id=str(pay.pk),entry__organization_id=org.pk)
        self.assertEqual(pay_lines.count(),4)                                                           # split per allocation
        by_period={}
        for l in pay_lines.filter(account='CUSTOMER_RECEIVABLE'):by_period[l.period_id]=l.credit
        self.assertEqual(by_period,{periods[0].pk:D(100),periods[1].pk:D(50)})
        self.assertEqual(Entry.objects.get(source_type__startswith='payment',source_id=str(adv.pk)).source_type,'payment_advance')
        owner_line=Line.objects.get(entry__source_type='expense',account='OWNER_PAYABLE');self.assertEqual((owner_line.party_id,owner_line.period_id,owner_line.vehicle_id),(o.pk,periods[1].pk,car.pk))
        self.assertEqual(before,sum(l.debit-l.credit for l in Line.objects.filter(account='CASH')))   # totals unchanged
