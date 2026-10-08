from datetime import date
from ninja import Query, Router
from ..models import Payment
from .. import policies, services
from ._shared import PaymentIn, membership, result, payment_dict, paginate

router = Router()

@router.post('/orgs/{org_id}/payments')
def record_payment(request,org_id:int,payload:PaymentIn):
    m=membership(request,org_id,'payment.post',obligations=[a.obligation_id for a in payload.allocations])
    p=result(services.record_payment,m.organization,request.user,payload.dict(),request.headers.get('Idempotency-Key',''))
    return payment_dict(Payment.objects.prefetch_related('allocations').get(pk=p.pk))

@router.get('/orgs/{org_id}/payments')
def payments(request,org_id:int,from_:date|None=Query(None,alias='from'),to:date|None=None,branch_id:int|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read_financial')
    start,end=result(services.resolve_range,from_,to)
    qs=services.entry_dates(policies.branch_scope(m,Payment.objects.filter(organization_id=org_id),branch_id),start,end)
    return paginate(qs.prefetch_related('allocations').order_by('-id'),limit,offset,lambda p:payment_dict(p))
