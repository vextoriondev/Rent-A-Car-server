from decimal import Decimal
from django.db import migrations

DEFAULTS=[('fuel','Fuel / gas'),('toll','Toll / parking'),('maintenance','Routine maintenance'),('repair','Repairs'),('damage','Damage'),('cleaning','Cleaning'),('driver_allowance','Driver allowance'),('other','Miscellaneous')]

def seed(apps,schema_editor):
    Org=apps.get_model('rentals','Organization');Cat=apps.get_model('rentals','ExpenseCategory');Expense=apps.get_model('rentals','Expense');Approval=apps.get_model('rentals','Approval')
    for org in Org.objects.all():
        for code,name in DEFAULTS:Cat.objects.get_or_create(organization=org,code=code,defaults={'name':name})
    for e in Expense.objects.select_related('period').all():
        org_id=e.period.organization_id
        cat,_=Cat.objects.get_or_create(organization_id=org_id,code=e.category,defaults={'name':e.category.replace('_',' ').title()})
        e.category_ref=cat;e.save(update_fields=['category_ref'])
        if e.state=='pending' or e.approved_by_id:
            Approval.objects.get_or_create(organization_id=org_id,subject_type='expense',subject_id=str(e.pk),defaults={'requested_by_id':e.recorded_by_id,'decided_by_id':e.approved_by_id,'decision':'approved' if e.approved_by_id else 'pending','threshold_at_time':Decimal('5000.00')})

class Migration(migrations.Migration):
    dependencies=[('rentals','0008_expense_rules_approvals')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
