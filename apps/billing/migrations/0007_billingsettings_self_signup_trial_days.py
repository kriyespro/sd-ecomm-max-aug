from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0006_no_platform_branding"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingsettings",
            name="self_signup_trial_days",
            field=models.PositiveIntegerField(
                default=7,
                help_text="Trial length for stores opened through public self-signup.",
            ),
        ),
        migrations.AlterField(
            model_name="billingsettings",
            name="trial_days",
            field=models.PositiveIntegerField(
                default=14,
                help_text="Trial length for stores a partner / the platform sets up.",
            ),
        ),
    ]
