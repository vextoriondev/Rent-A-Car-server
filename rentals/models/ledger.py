from django.db import models
from .organizations import Organization
from .parties import Party, Vehicle
from .agreements import Agreement, Period

class Account(models.Model):
    """Chart of accounts, per organization. JournalLine.account (the code string) is kept in step with account_ref."""
    class Type(models.TextChoices): ASSET='asset','Asset'; LIABILITY='liability','Liability'; REVENUE='revenue','Revenue'; EXPENSE='expense','Expense'; EQUITY='equity','Equity'
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE,related_name='accounts')
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=10,choices=Type.choices)
    subledger_kind = models.CharField(max_length=10,blank=True,help_text='owner/driver/customer when the account is a party sub-ledger')
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','code'],name='unique_account_code_org')]

class JournalEntry(models.Model):
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    source_type = models.CharField(max_length=30)
    source_id = models.CharField(max_length=40)
    posted_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['organization','source_type','source_id'],name='unique_journal_source')]

class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry,on_delete=models.PROTECT,related_name='lines')
    account = models.CharField(max_length=40)
    account_ref = models.ForeignKey(Account,null=True,blank=True,on_delete=models.PROTECT,related_name='lines')
    debit = models.DecimalField(max_digits=14,decimal_places=2,default=0)
    credit = models.DecimalField(max_digits=14,decimal_places=2,default=0)
    party = models.ForeignKey(Party,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    agreement = models.ForeignKey(Agreement,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    period = models.ForeignKey(Period,null=True,blank=True,on_delete=models.PROTECT,related_name='journal_lines')
    vehicle = models.ForeignKey(Vehicle,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    class Meta: indexes=[models.Index(fields=['period','account'])]
