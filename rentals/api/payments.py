from ninja import Router
from ..models import Payment
from .. import services
from ._shared import PaymentIn, membership, result, payment_dict

router = Router()

@router.post('/orgs/{org_id}/payments')
def record_payment(request,org_id:int,payload:PaymentIn):
    m=membership(request,org_id,('admin','accountant'))
    p=result(services.record_payment,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''))
    return payment_dict(Payment.objects.prefetch_related('allocations').get(pk=p.pk))

@router.get('/orgs/{org_id}/payments')
def payments(request,org_id:int):
    membership(request,org_id)
    return [payment_dict(p) for p in Payment.objects.filter(organization_id=org_id).prefetch_related('allocations').order_by('-id')[:200]]
