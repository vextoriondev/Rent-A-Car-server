"""The single audit helper every service calls after a state change, inside the same transaction."""
import json
from ..audit_context import get_context
from ..models import AuditEvent

def _plain(value):
    return None if value is None else json.loads(json.dumps(value,default=str))

def audit(org,user,action,target_type='',target_id='',before=None,after=None,reason=''):
    ctx=get_context()
    return AuditEvent.objects.create(organization=org,actor=user if getattr(user,'pk',None) else None,action=action,target_type=target_type,target_id=str(target_id),
        before=_plain(before),after=_plain(after),reason=(reason or '')[:240],request_id=ctx.get('request_id',''),ip=ctx.get('ip'))

def snap(obj,fields):
    """Plain dict of the named attributes (money and dates are stringified by audit)."""
    return {f:getattr(obj,f) for f in fields}
