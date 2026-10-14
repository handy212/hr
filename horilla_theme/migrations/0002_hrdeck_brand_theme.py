from django.db import migrations


BRAND_COLORS = {
    "description": "HRDeck navy, blue, teal, coral and light gray palette",
    "primary_50": "#F4F6FA",
    "primary_100": "#EAF3FF",
    "primary_200": "#D7E9FF",
    "primary_300": "#B8D8FF",
    "primary_400": "#8FC2FF",
    "primary_500": "#5AA6FF",
    "primary_600": "#2D8CFF",
    "primary_700": "#1C70D9",
    "primary_800": "#1254AA",
    "primary_900": "#0F2D5B",
    "dark_50": "#F4F6FA",
    "dark_100": "#D6DFEB",
    "dark_200": "#9AAFC9",
    "dark_300": "#6D829E",
    "dark_400": "#536987",
    "dark_500": "#2C466B",
    "dark_600": "#0F2D5B",
    "secondary_50": "#F0FCFA",
    "secondary_100": "#E3F8F5",
    "secondary_200": "#C8F1EC",
    "secondary_300": "#A1E7DF",
    "secondary_400": "#75D9CD",
    "secondary_500": "#39C8B9",
    "secondary_600": "#14B8A6",
    "secondary_700": "#0E9487",
    "secondary_800": "#0C756B",
    "secondary_900": "#07584F",
}


def apply_brand_theme(apps, schema_editor):
    Theme = apps.get_model("horilla_theme", "HorillaColorTheme")
    CompanyTheme = apps.get_model("horilla_theme", "CompanyTheme")
    Theme.objects.filter(is_default=True).update(is_default=False)
    theme, _ = Theme.objects.update_or_create(
        name="HRDeck Brand", defaults={**BRAND_COLORS, "is_default": True}
    )
    # This deployment is being rebranded as a whole. Existing company theme
    # assignments must point at the new palette for the change to be visible.
    CompanyTheme.objects.exclude(theme_id=theme.pk).update(theme_id=theme.pk)


class Migration(migrations.Migration):
    dependencies = [("horilla_theme", "0001_initial")]

    operations = [migrations.RunPython(apply_brand_theme, migrations.RunPython.noop)]
