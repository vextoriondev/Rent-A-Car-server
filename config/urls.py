from django.contrib import admin
from django.http import HttpResponse
from django.urls import path
from rentals.api import api
urlpatterns = [path('healthz', lambda request: HttpResponse('ok', content_type='text/plain')), path('admin/', admin.site.urls), path('api/v1/', api.urls)]
