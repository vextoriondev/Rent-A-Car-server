from django.conf import settings
from django.db import models

LANGUAGE_CHOICES=[('en','English'),('bn','Bangla')]

class UserPreference(models.Model):
    """Per-person choices for staff logins. `language` is the language of their screens and exports (M17)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='rentflow_preference')
    language = models.CharField(max_length=2,choices=LANGUAGE_CHOICES,default='en')
