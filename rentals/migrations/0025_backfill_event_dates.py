from django.db import migrations

def forwards(apps,schema_editor):
    from rentals.services.event_dates import backfill_event_dates
    backfill_event_dates({k:apps.get_model('rentals',k) for k in ('Charge','Expense','JournalEntry','JournalLine','Payment')})

class Migration(migrations.Migration):
    dependencies=[('rentals','0024_journal_event_date')]
    operations=[migrations.RunPython(forwards,migrations.RunPython.noop)]
