from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0003_phase2"),
    ]

    operations = [
        migrations.AddField(
            model_name="workerprofile",
            name="phone",
            field=models.CharField(blank=True, db_index=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="workerprofile",
            name="last_latitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workerprofile",
            name="last_longitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workerprofile",
            name="location_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employerprofile",
            name="phone",
            field=models.CharField(blank=True, db_index=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="employerprofile",
            name="last_latitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employerprofile",
            name="last_longitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employerprofile",
            name="location_label",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="employerprofile",
            name="location_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
