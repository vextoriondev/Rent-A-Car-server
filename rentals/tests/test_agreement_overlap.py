import unittest
from decimal import Decimal
from ..models import Agreement
from .. import services
from .base import RentalsTestCase

class AgreementOverlapGapTests(RentalsTestCase):
    """Gap #3 (BACKEND_MODEL_PLAN.md): only the create_agreement API view checks for an overlapping
    active agreement on the same vehicle (via a select_for_update + filter in rentals/api/agreements.py);
    the domain layer (services.validate_agreement) enforces no such rule, so anything that creates an
    Agreement without going through that one view - like agreements/quick, or a future service - can
    still produce two overlapping agreements for one vehicle. Expected to start passing once M2 adds
    VehicleOwnership/overlap validation to the domain layer itself."""
    @unittest.expectedFailure
    def test_domain_layer_rejects_overlapping_vehicle_agreements(self):
        second=Agreement(organization=self.org,vehicle=self.car,customer=self.customer,driver=self.driver,
            start_date=self.agreement.start_date,customer_rent=Decimal('50000'),owner_rent=Decimal('30000'),driver_salary=Decimal('10000'))
        services.validate_agreement(second)
        second.save()
        overlapping=Agreement.objects.filter(vehicle=self.car,active=True).exclude(pk=self.agreement.pk)
        self.assertFalse(overlapping.exists(),'the domain layer should reject a second active agreement on the same vehicle')
