"""Server text in the user's language (M17). Every sentence a person can read comes from a catalog keyed by a stable code.

`translate('period_closed', month='Oct 2026')` returns the sentence in the current language (set per request by
`LanguageMiddleware`), falling back to English. Codes and parameters travel to the client too, so a screen can show the
server's own sentence or translate the code itself. A text that is not in the catalog is shown as it is (legacy callers)."""
import json
from contextvars import ContextVar
from pathlib import Path

LANGUAGES=('en','bn')
DEFAULT='en'
_LOCALE=Path(__file__).parent/'locale'
_catalogs:dict[str,dict[str,str]]={}
_current:ContextVar[str|None]=ContextVar('rentflow_language',default=None)
_resolver:ContextVar=ContextVar('rentflow_language_resolver',default=None)

def catalog(lang:str)->dict[str,str]:
    if lang not in _catalogs:
        path=_LOCALE/f'{lang}.json'
        _catalogs[lang]=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return _catalogs[lang]

def reload_catalogs():
    _catalogs.clear()

def current_language()->str:
    """The language of this request. The signed-in person's saved choice is looked up only when a sentence is actually needed (and only once)."""
    lang=_current.get()
    if lang:return lang
    resolver=_resolver.get()
    if resolver:
        lang=resolver()
        lang=lang if lang in LANGUAGES else DEFAULT
        _current.set(lang);_resolver.set(None)   # asked once per request, even when the person has no saved choice
        return lang
    return DEFAULT

def set_language(lang:str|None,resolver=None):
    """Returns the tokens to hand back to `reset_language`. An unknown language means: ask `resolver` when a sentence is needed, else English."""
    return _current.set(lang if lang in LANGUAGES else None),_resolver.set(resolver)

def reset_language(tokens):
    _current.reset(tokens[0]);_resolver.reset(tokens[1])

def has_code(code:str)->bool:
    return code in catalog(DEFAULT)

class _Safe(dict):
    def __missing__(self,key):return '{'+key+'}'

def translate(code:str,params:dict|None=None,lang:str|None=None)->str:
    """The sentence for `code` in `lang` (default: the current language); English when a translation is missing; the code itself (formatted) when unknown."""
    lang=lang if lang in LANGUAGES else current_language()
    text=catalog(lang).get(code) or catalog(DEFAULT).get(code) or code
    if params:
        try:return text.format_map(_Safe(params))
        except (ValueError,IndexError):return text
    return text

def pick_language(header:str|None)->str|None:
    """The first language we support in an Accept-Language header such as `bn-BD,bn;q=0.9,en;q=0.8`."""
    for part in (header or '').split(','):
        tag=part.split(';')[0].strip().lower()[:2]
        if tag in LANGUAGES:return tag
    return None

class LanguageMiddleware:
    """Language of the request: ?lang=, then the Accept-Language header the app sends, then the user's saved choice, then English."""
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        lang=request.GET.get('lang') if request.GET.get('lang') in LANGUAGES else None
        lang=lang or pick_language(request.headers.get('Accept-Language'))
        def saved_choice():
            user=getattr(request,'user',None)
            if user is None or not user.is_authenticated:return None
            from .models import UserPreference
            return UserPreference.objects.filter(user=user).values_list('language',flat=True).first()
        tokens=set_language(lang,None if lang else saved_choice)
        try:return self.get_response(request)
        finally:reset_language(tokens)


def word(prefix:str,code:str,default:str|None=None)->str:
    """A catalog word for a stored code, e.g. word('category','repair'); readable text when the catalog has no entry."""
    key=f'{prefix}_{code}'
    return translate(key) if has_code(key) else (default if default is not None else str(code).replace('_',' ').capitalize())

def format_month(d)->str:
    """`October 2026` in the current language."""
    return translate('month_format',{'month':translate(f'month_{d.month}'),'year':d.year})
