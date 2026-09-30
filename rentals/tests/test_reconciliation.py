from decimal import Decimal
from ..models import JournalLine
from .. import services
from .base import RentalsTestCase

class LedgerReconciliationTests(RentalsTestCase):
    """Gap #5 (BACKEND_MODEL_PLAN.md): statement() is derived from obligations/allocations, not the
    journal. Until M4 introduces a real chart of accounts and ledger_statement(), this test proves
    the two views agree for the worked example so a future regression is caught."""
    def account_balance(self,account,increases):
        lines=JournalLine.objects.filter(entry__organization=self.org,account=account)
        debit=sum((l.debit for l in lines),Decimal('0'))
        credit=sum((l.credit for l in lines),Decimal('0'))
        return (debit-credit) if increases=='debit' else (credit-debit)

    def test_statement_matches_journal_for_worked_example(self):
        data={'period_id':self.period.pk,'category':'repair','amount':Decimal('8000'),'paid_by':'operator','responsible':'owner','offset_owner':True,'reference':'GAR-0182'}
        expense=services.record_expense(self.org,self.user,data,'expense-recon-1')
        services.approve_expense(self.org,self.user,expense.pk)
        self.pay('customer_payment','40000','CUST-R1')
        self.pay('customer_payment','30000','CUST-R2')
        self.pay('owner_payout','40000','OWNER-R1')
        self.pay('driver_payout','15000','DRIVER-R1')
        s=services.statement(self.period)

        customer_due=self.account_balance('CUSTOMER_RECEIVABLE','debit')
        owner_due=self.account_balance('OWNER_PAYABLE','credit')
        driver_due=self.account_balance('DRIVER_PAYABLE','credit')

        self.assertEqual(customer_due,s['customer']['due'])
        self.assertEqual(owner_due,s['owner']['due'])
        self.assertEqual(driver_due,s['driver']['due'])
        self.assertEqual(customer_due,Decimal('30000'))
        self.assertEqual(owner_due,Decimal('12000'))
        self.assertEqual(driver_due,Decimal('5000'))
