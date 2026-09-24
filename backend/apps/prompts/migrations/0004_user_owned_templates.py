import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("prompts", "0003_sales_call_v2"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="prompttemplate",
            name="prompt_slug_version_unique",
        ),
        migrations.RemoveConstraint(
            model_name="prompttemplate",
            name="prompt_one_active_version_per_slug",
        ),
        migrations.AddField(
            model_name="prompttemplate",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="prompt_templates",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="prompttemplate",
            constraint=models.UniqueConstraint(
                condition=models.Q(owner__isnull=True),
                fields=("slug", "version"),
                name="prompt_builtin_slug_version_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="prompttemplate",
            constraint=models.UniqueConstraint(
                condition=models.Q(owner__isnull=False),
                fields=("owner", "slug", "version"),
                name="prompt_user_slug_version_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="prompttemplate",
            constraint=models.UniqueConstraint(
                condition=models.Q(active=True, owner__isnull=True),
                fields=("slug",),
                name="prompt_builtin_active_slug_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="prompttemplate",
            constraint=models.UniqueConstraint(
                condition=models.Q(active=True, owner__isnull=False),
                fields=("owner", "slug"),
                name="prompt_user_active_slug_unique",
            ),
        ),
    ]
