"""Versioned, session-authenticated API. Money is serialized as decimal strings."""
from ninja import NinjaAPI
from ninja.security import django_auth
from ..services.helpers import DomainError
from .errors import ApiError
from . import auth, parties, agreements, expenses, payments, notifications, snapshot, billing, settlements, documents, reports, driver_agreements, portal, tasin

api=NinjaAPI(auth=django_auth,title='RentFlow API',version='1.0.0')

for module in (auth, parties, agreements, expenses, payments, notifications, snapshot, billing, settlements, documents, reports, driver_agreements, portal, tasin):
    api.add_router('',module.router)


def _body(exc):
    return {'detail':str(exc.message if isinstance(exc,ApiError) else exc),'code':exc.code,'params':{k:str(v) for k,v in exc.params.items()}}

@api.exception_handler(ApiError)
def api_error(request,exc):
    """Every error answers `{detail, code, params}`: the sentence in the user's language plus the code a screen can translate itself."""
    return api.create_response(request,_body(exc),status=exc.status_code)

@api.exception_handler(DomainError)
def domain_error(request,exc):
    return api.create_response(request,_body(exc),status=422)
