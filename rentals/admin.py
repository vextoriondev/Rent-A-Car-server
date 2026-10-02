"""Only organization setup is editable in Django admin; monetary actions use services."""
from django.contrib import admin
from .models import Organization,Branch,Membership,Party,Contact,PartyUser,Vehicle
admin.site.register(Organization)
admin.site.register(Membership)
admin.site.register(Party)
admin.site.register(Vehicle)
admin.site.register(Branch)
admin.site.register(Contact)
admin.site.register(PartyUser)
