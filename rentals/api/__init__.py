"""Versioned, session-authenticated API. Money is serialized as decimal strings."""
from ninja import NinjaAPI
from ninja.security import django_auth
from . import auth, parties, agreements, expenses, payments, notifications, snapshot, billing, settlements

api=NinjaAPI(auth=django_auth,title='RentFlow API',version='1.0.0')

for module in (auth, parties, agreements, expenses, payments, notifications, snapshot, billing, settlements):
    api.add_router('',module.router)
