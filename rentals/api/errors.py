"""API errors that carry a message code (M17). The body is `{"detail": <sentence in the user's language>, "code": ..., "params": {...}}`,
so a screen can show the server's sentence or translate the code itself."""
from ninja.errors import HttpError
from ..i18n import translate
from ..services.helpers import DomainError

class ApiError(HttpError):
    """`ApiError(422, 'period_closed', month='Oct 2026')` or `ApiError(422, domain_error)` (keeps that error's code and values)."""
    def __init__(self,status_code:int,code,**params):
        if isinstance(code,DomainError):
            params=dict(code.params);code=code.code
        self.code=code
        self.params=params
        super().__init__(status_code,translate(code,params))
