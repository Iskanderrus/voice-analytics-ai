"""sales-call v2: drop "subscription" from the topic filter.

v1 matched complaint calls such as "subscription cancellation" and outranked
support-call (priority 20 vs 10). Templates are immutable, so the fix is a new
version; v1 stays in place (inactive) for the results that reference it.
"""

from django.db import migrations

REMOVED_TERMS = {"subscription"}


def forwards(apps, schema_editor):
    PromptTemplate = apps.get_model("prompts", "PromptTemplate")
    v1 = PromptTemplate.objects.filter(slug="sales-call", version=1).first()
    if v1 is None or PromptTemplate.objects.filter(slug="sales-call", version=2).exists():
        return
    terms = [t for t in v1.filter_config["topics_contains_any"] if t not in REMOVED_TERMS]
    PromptTemplate.objects.filter(slug="sales-call", active=True).update(active=False)
    PromptTemplate.objects.create(
        name=v1.name,
        slug=v1.slug,
        version=2,
        active=True,
        priority=v1.priority,
        analysis_type=v1.analysis_type,
        system_instructions=v1.system_instructions,
        analysis_instructions=v1.analysis_instructions,
        filter_config={**v1.filter_config, "topics_contains_any": terms},
        output_schema=v1.output_schema,
    )


def backwards(apps, schema_editor):
    PromptTemplate = apps.get_model("prompts", "PromptTemplate")
    v2 = PromptTemplate.objects.filter(slug="sales-call", version=2).first()
    if v2 is None:
        return
    # Results reference templates with PROTECT; keep a used v2 rather than fail.
    v2.active = False
    v2.save(update_fields=["active"])
    PromptTemplate.objects.filter(slug="sales-call", version=1).update(active=True)
    if not v2.results.exists():
        v2.delete()


class Migration(migrations.Migration):
    dependencies = [("prompts", "0002_seed_templates")]

    operations = [migrations.RunPython(forwards, backwards)]
