from decimal import Decimal
from ..models import Expense, OwnerOffset
from .. import services
from .base import RentalsTestCase

class ReversalTests(RentalsTestCase):
    def _post_owner_offset_expense(self,reference):
        data={'period_id':self.period.pk,'category':'repair','amount':Decimal('8000'),'paid_by':'operator','responsible':'owner','offset_owner':True,'reference':reference}
        expense=services.record_expense(self.org,self.user,data,reference)
        services.approve_expense(self.org,self.user,expense.pk)
        return expense

    def test_reversal_restores_owner_balance(self):
        before=services.statement(self.period)['owner']['due']
        expense=self._post_owner_offset_expense('GAR-REV-1')
        self.assertEqual(services.statement(self.period)['owner']['due'],before-Decimal('8000'))
        services.reverse_expense(self.org,self.user,expense.pk,'Duplicate entry')
        expense.refresh_from_db()
        self.assertEqual(expense.state,Expense.State.REVERSED)
        self.assertEqual(services.statement(self.period)['owner']['due'],before)

    def test_owner_offset_removed_from_statement_on_reversal(self):
        expense=self._post_owner_offset_expense('GAR-REV-2')
        self.assertEqual(services.statement(self.period)['owner']['offsets'],Decimal('8000'))
        self.assertTrue(OwnerOffset.objects.filter(expense=expense).exists())
        services.reverse_expense(self.org,self.user,expense.pk,'Duplicate entry')
        self.assertEqual(services.statement(self.period)['owner']['offsets'],Decimal('0.00'))

    def test_reversed_expense_cannot_be_reversed_again(self):
        expense=self._post_owner_offset_expense('GAR-REV-3')
        services.reverse_expense(self.org,self.user,expense.pk,'Duplicate entry')
        with self.assertRaises(services.DomainError):
            services.reverse_expense(self.org,self.user,expense.pk,'Second attempt')
