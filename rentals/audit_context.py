"""Per-request context (request id, client ip) that services read when writing audit events, without every service
having to take the HTTP request. Set by RequestContextMiddleware; empty outside a request (management commands, tests)."""
import uuid
from contextvars import ContextVar

_ctx:ContextVar[dict]=ContextVar('rentflow_request_context',default={})

def get_context() -> dict:
    return _ctx.get()

class RequestContextMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        forwarded=request.META.get('HTTP_X_FORWARDED_FOR','').split(',')[0].strip()
        token=_ctx.set({'request_id':request.headers.get('X-Request-ID','')[:40] or uuid.uuid4().hex[:16],'ip':forwarded or request.META.get('REMOTE_ADDR') or None})
        try:return self.get_response(request)
        finally:_ctx.reset(token)
