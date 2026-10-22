"""Commercial feature migration preserves existing customers' module access."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class EntitlementMigrationTests(TransactionTestCase):
    def test_existing_published_plan_keeps_all_current_modules(self):
        before = [("saas", "0007_alter_billingpayment_status")]
        after = MigrationExecutor(connection).loader.graph.leaf_nodes("saas")
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            old_apps = executor.loader.project_state(before).apps
            plan = old_apps.get_model("saas", "BillingPlan").objects.create(
                name="Existing commercial plan",
                amount=12345,
                currency="NGN",
                interval="monthly",
                publication_state="published",
                provider_domain="live",
                provider_code="PLN_migration",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            apps = executor.loader.project_state(after).apps
            features = set(
                apps.get_model("saas", "BillingPlanFeature")
                .objects.filter(plan_id=plan.pk)
                .values_list("feature", flat=True)
            )
            self.assertEqual(
                features,
                {
                    "attendance",
                    "leave",
                    "payroll",
                    "recruitment",
                    "onboarding",
                    "offboarding",
                    "performance",
                    "projects",
                    "assets",
                    "helpdesk",
                    "devices",
                    "reports",
                },
            )
            preserved = apps.get_model("saas", "BillingPlan").objects.get(pk=plan.pk)
            self.assertEqual(
                (
                    preserved.amount,
                    preserved.publication_state,
                    preserved.provider_code,
                ),
                (12345, "published", "PLN_migration"),
            )
        finally:
            MigrationExecutor(connection).migrate(after)
