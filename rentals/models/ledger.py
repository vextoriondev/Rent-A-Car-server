from django.db import models
from .organizations import Organization

class JournalEntry(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    source_type = models.CharField(max_length=30)
    source_id = models.CharField(max_length=40)
    posted_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','source_type','source_id'],name='unique_journal_source')]

class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry,on_delete=models.PROTECT,related_name='lines')
    account = models.CharField(max_length=40)
    debit = models.DecimalField(max_digits=14,decimal_places=2,default=0)
    credit = models.DecimalField(max_digits=14,decimal_places=2,default=0)
