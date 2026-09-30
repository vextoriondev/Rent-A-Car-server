import threading
import unittest
from decimal import Decimal
from django.db import connection
from django.test import TransactionTestCase
from .. import services
from .base import build_worked_fixture

@unittest.skipUnless(connection.vendor=='postgresql','SQLite ignores select_for_update; run with docker compose up -d and POSTGRES_* env (see backend/README.md)')
class ConcurrentPaymentTests(TransactionTestCase):
    """M0: select_for_update on the obligation row must stop two concurrent payments from
    over-allocating it. Each thread needs its own DB connection, so this only proves anything
    under a real database that takes row locks - SQLite is skipped."""
    def setUp(self):
        fixture=build_worked_fixture()
        self.org=fixture['org']
        self.user=fixture['user']
        self.obligation=fixture['ob']['customer_rent']
        connection.close()

    def test_two_concurrent_payments_cannot_over_allocate_one_obligation(self):
        results=[]
        def attempt(reference):
            try:
                services.record_payment(self.org,self.user,{'kind':'customer_payment','amount':Decimal('60000'),
                    'reference':reference,'method':'bank','event_date':'2026-09-20',
                    'allocations':[{'obligation_id':self.obligation.pk,'amount':Decimal('60000')}]},reference)
                results.append('ok')
            except services.DomainError:
                results.append('rejected')
            finally:
                connection.close()

        barrier=threading.Barrier(2)
        def run(reference):
            barrier.wait()
            attempt(reference)

        threads=[threading.Thread(target=run,args=(ref,)) for ref in ('CONC-1','CONC-2')]
        for t in threads:t.start()
        for t in threads:t.join()

        self.assertEqual(sorted(results),['ok','rejected'])
        self.obligation.refresh_from_db()
        allocated=services.total(self.obligation.allocations.all())
        self.assertLessEqual(allocated,self.obligation.amount)
