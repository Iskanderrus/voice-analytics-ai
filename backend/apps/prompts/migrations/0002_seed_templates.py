from django.db import migrations

SALES = {
    "name": "Sales Call Analysis",
    "slug": "sales-call",
    "version": 1,
    "priority": 20,
    "analysis_type": "sales_call",
    "system_instructions": "You are an experienced B2B sales analyst reviewing a sales conversation.",
    "analysis_instructions": (
        "Extract from the conversation:\n"
        "- objections: concerns or reasons not to buy raised by the prospect\n"
        "- buying_signals: statements showing interest, budget, urgency or intent to purchase\n"
        "- competitors: competitor products or vendors mentioned\n"
        "- commitments: things either side explicitly committed to\n"
        "- next_steps: agreed next steps, with dates if mentioned\n"
        "- summary: 2-3 sentences on where the deal stands.\n"
        "Each list item is one short sentence. Use an empty list when nothing applies."
    ),
    "filter_config": {
        "topics_contains_any": [
            "pricing", "price", "purchase", "contract", "quote", "discount", "deal",
            "renewal", "subscription", "licens", "sales",
        ]
    },
    "output_schema": {
        "fields": {
            "objections": "Concerns or reasons not to buy raised by the prospect",
            "buying_signals": "Statements indicating interest, budget, urgency or intent",
            "competitors": "Competitor products or vendors mentioned",
            "commitments": "Explicit commitments made by either side",
            "next_steps": "Agreed next steps, with dates if mentioned",
        }
    },
}

SUPPORT = {
    "name": "Support Call Analysis",
    "slug": "support-call",
    "version": 1,
    "priority": 10,
    "analysis_type": "support_call",
    "system_instructions": "You are a customer-support quality analyst reviewing a support call.",
    "analysis_instructions": (
        "Extract from the conversation:\n"
        "- issues: the problems the customer reported\n"
        "- troubleshooting_steps: steps taken or suggested to diagnose or fix them\n"
        "- resolution: how each issue was resolved, or that it remains open\n"
        "- follow_ups: actions promised to the customer, with owner if named\n"
        "- frustration_signals: moments where the customer expressed frustration\n"
        "- summary: 2-3 sentences on the customer's problem and its status.\n"
        "Each list item is one short sentence. Use an empty list when nothing applies."
    ),
    "filter_config": {
        "topics_contains_any": [
            "support", "issue", "problem", "bug", "error", "outage", "refund",
            "complaint", "troubleshoot", "broken", "not working", "login", "delivery",
        ]
    },
    "output_schema": {
        "fields": {
            "issues": "Problems the customer reported",
            "troubleshooting_steps": "Steps taken or suggested to diagnose or fix the issues",
            "resolution": "How each issue was resolved, or that it remains open",
            "follow_ups": "Actions promised to the customer",
            "frustration_signals": "Moments where the customer expressed frustration",
        }
    },
}


def seed(apps, schema_editor):
    PromptTemplate = apps.get_model("prompts", "PromptTemplate")
    for template in (SALES, SUPPORT):
        PromptTemplate.objects.get_or_create(
            slug=template["slug"], version=template["version"], defaults=template
        )


def unseed(apps, schema_editor):
    PromptTemplate = apps.get_model("prompts", "PromptTemplate")
    PromptTemplate.objects.filter(slug__in=["sales-call", "support-call"], version=1).delete()


class Migration(migrations.Migration):
    dependencies = [("prompts", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
