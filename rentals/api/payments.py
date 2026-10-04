from ninja import Router
from ..models import Payment
from .. import services
from ._shared import PaymentIn, membership, result, payment_dict, paginate

router = Router()

@router.post('/orgs/{org_id}/payments')
def record_payment(request,org_id:int,payload:PaymentIn):
    m=membership(request,org_id,'payment.post')
    p=result(services.record_payment,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''))
    return payment_dict(Payment.objects.prefetch_related('allocations').get(pk=p.pk))

@router.get('/orgs/{org_id}/payments')
def payments(request,org_id:int,limit:int=100,offset:int=0):
    membership(request,org_id,'read_financial')
    return paginate(Payment.objects.filter(organization_id=org_id).prefetch_related('allocations').order_by('-id'),limit,offset,lambda p:payment_dict(p))
