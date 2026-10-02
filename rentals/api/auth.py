from django.contrib.auth import authenticate, get_user_model, login as auth_login, logout as auth_logout
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from ninja import Router
from ninja.errors import HttpError
from ninja.utils import check_csrf
from .. import policies, services
from ..models import Organization, Membership, PartyUser
from ._shared import LoginIn, RegisterIn

router = Router()

def require_csrf(request:HttpRequest):
    forbidden=check_csrf(request)
    if forbidden:raise HttpError(403,'Session security check failed. Reload the page and try again.')

@router.get('/me')
def me(request):
    links={}
    for link in PartyUser.objects.filter(user=request.user,active=True).select_related('party'):links.setdefault(link.organization_id,[]).append({'id':link.party_id,'kind':link.party.kind,'name':link.party.name})
    return {'user_id':request.user.pk,'username':request.user.get_username(),'organizations':[{'id':m.organization_id,'name':m.organization.name,'role':m.role,'branch':{'id':m.branch_id,'name':m.branch.name} if m.branch_id else None,'parties':links.get(m.organization_id,[]),'actor':policies.is_actor(m.role)} for m in Membership.objects.filter(user=request.user).select_related('organization','branch')]}

@router.get('/csrf',auth=None)
@ensure_csrf_cookie
@csrf_exempt
def csrf_cookie(request):
    return HttpResponse(status=204)

@router.post('/auth/login',auth=None)
def login_view(request,payload:LoginIn):
    require_csrf(request)
    user=authenticate(request,username=payload.username.strip(),password=payload.password)
    if not user:raise HttpError(401,'Incorrect username or password.')
    auth_login(request,user)
    return {'user_id':user.pk,'username':user.get_username()}

@router.post('/auth/logout',auth=None)
def logout_view(request):
    require_csrf(request)
    auth_logout(request)
    return HttpResponse(status=204)

@router.post('/auth/register',auth=None)
def register_view(request,payload:RegisterIn):
    require_csrf(request)
    org_name=payload.organization_name.strip()
    username=payload.username.strip()
    if not org_name:raise HttpError(422,'Company or workspace name is required.')
    if not username:raise HttpError(422,'Username is required.')
    if len(payload.password)<8:raise HttpError(422,'Password must be at least 8 characters.')
    User=get_user_model()
    if User.objects.filter(username__iexact=username).exists():raise HttpError(409,'That username is already taken.')
    with transaction.atomic():
        user=User.objects.create_user(username=username,email=payload.email.strip(),password=payload.password)
        org=Organization.objects.create(name=org_name)
        Membership.objects.create(organization=org,user=user,role=Membership.Role.ADMIN,branch=services.default_branch(org))
    auth_login(request,user)
    return {'user_id':user.pk,'username':user.get_username(),'organization':{'id':org.pk,'name':org.name}}
