"""A payout is saved at once (no approval); the cashbook shows who recorded it and an optional note."""
from datetime import date
from decimal import Decimal
from django.test import Client
from .. import services
from .base import RentalsTestCase

D=Decimal

class PaymentNoteTests(RentalsTestCase):
    def pay(self,ref,**extra):
        return services.record_payment(self.org,self.user,{'kind':'owner_payout','amount':D(1000),'reference':ref,'event_date':date(2026,9,10),'allocations':[{'obligation_id':self.ob['owner_rent'].pk,'amount':D(1000)}],**extra},ref)

    def test_note_is_saved_and_blank_by_default(self):
        self.assertEqual(self.pay('N1',note='  September rent, first part ').note,'September rent, first part')
        self.assertEqual(self.pay('N2').note,'')

    def test_snapshot_shows_who_recorded_it_and_the_note(self):
        self.pay('N3',note='Rent to Farhana')
        c=Client();c.force_login(self.user)
        entries=c.get(f'/api/v1/orgs/{self.org.pk}/snapshot?month=2026-09-01').json()['entries']
        row=next(e for e in entries if e['type']=='Owner payout')
        self.assertEqual((row['description'],row['recordedBy']),('Rent to Farhana','accountant'))
