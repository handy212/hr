"""Keep SaaS ownership and its public integration contract explicit."""

from pathlib import Path

from django.apps import apps
from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import resolve, reverse


class PlatformBoundaryTests(SimpleTestCase):
    def test_tenant_model_is_owned_by_saas_with_existing_table(self):
        tenant = apps.get_model("saas", "Tenant")
        self.assertEqual(tenant.__module__, "saas.models")
        self.assertEqual(tenant._meta.db_table, "base_tenant")
        with self.assertRaises(LookupError):
            apps.get_model("base", "Tenant")

    def test_public_urls_resolve_to_platform_app(self):
        for name, args in [
            ("tenant-list", []),
            ("tenant-create", []),
            ("tenant-detail", [1]),
            ("tenant-edit", [1]),
            ("tenant-delete", [1]),
            ("tenant-impersonate", [1]),
            ("tenant-impersonation-stop", []),
        ]:
            self.assertEqual(
                resolve(reverse(name, args=args)).func.__module__, "saas.views"
            )

    def test_platform_templates_and_assets_are_owned_by_saas(self):
        template = get_template("saas/tenants/list.html", using="platform")
        self.assertIn("saas/templates/saas/", str(template.origin).replace("\\", "/"))
        asset = finders.find("saas/tenant_console.css")
        self.assertIsNotNone(asset)
        self.assertIn("saas/static/saas/", Path(asset).as_posix())
