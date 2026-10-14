from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[('rentals','0025_backfill_event_dates')]
    operations=[migrations.AddField(model_name='payment',name='note',field=models.CharField(blank=True,default='',help_text='Why the money moved; shown in the cashbook next to who recorded it',max_length=240),preserve_default=False)]
