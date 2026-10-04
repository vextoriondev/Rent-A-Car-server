from datetime import date
from django.http import HttpResponse
from ninja import Router
from ninja.errors import HttpError
from .. import services
from ..services import reports as report_service
from ._shared import membership, result

router = Router()

def _filters(**kw):
    return {k:v.isoformat() if isinstance(v,date) else v for k,v in kw.items() if v not in(None,'')}

@router.get('/orgs/{org_id}/report-types')
def report_types(request,org_id:int):
    membership(request,org_id,'read_financial')
    return [{'type':t,'title':report_service.PERIOD_REPORTS[t]['title'] if t in report_service.PERIOD_REPORTS else report_service.OTHER_REPORTS[t]} for t in report_service.REPORT_TYPES]

@router.get('/orgs/{org_id}/reports/{report_type}')
def report(request,org_id:int,report_type:str,month:date|None=None,month_from:date|None=None,month_to:date|None=None,agreement_id:int|None=None,vehicle_id:int|None=None,owner_id:int|None=None,driver_id:int|None=None,customer_id:int|None=None,as_of:date|None=None,limit:int=100,offset:int=0):
    m=membership(request,org_id,'read_financial')
    f=_filters(month=month,month_from=month_from,month_to=month_to,agreement_id=agreement_id,vehicle_id=vehicle_id,owner_id=owner_id,driver_id=driver_id,customer_id=customer_id)
    return result(report_service.run_report,m.organization,report_type,f,limit,offset,as_of)

@router.get('/orgs/{org_id}/reports/{report_type}/export')
def export(request,org_id:int,report_type:str,month:date|None=None,month_from:date|None=None,month_to:date|None=None,agreement_id:int|None=None,vehicle_id:int|None=None,owner_id:int|None=None,driver_id:int|None=None,customer_id:int|None=None,as_of:date|None=None):
    m=membership(request,org_id,'export')
    f=_filters(month=month,month_from=month_from,month_to=month_to,agreement_id=agreement_id,vehicle_id=vehicle_id,owner_id=owner_id,driver_id=driver_id,customer_id=customer_id)
    text=result(report_service.report_csv,m.organization,report_type,f,as_of)
    services.audit(m.organization,request.user,'report.export','report',report_type,after={'filters':f})
    resp=HttpResponse(text,content_type='text/csv; charset=utf-8')
    resp['Content-Disposition']=f'attachment; filename="rentflow-{report_type}.csv"'
    resp['Cache-Control']='private, no-store'
    return resp
