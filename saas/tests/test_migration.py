"""Upgrade and rollback retain tenant rows and generic-reference identity."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TenantOwnershipMigrationTests(TransactionTestCase):
    def test_upgrade_and_rollback_preserve_existing_tenant_and_content_type(self):
        before = [("base", "0018_tenant_deleted_at"), ("saas", None)]
        after = MigrationExecutor(connection).loader.graph.leaf_nodes("saas")
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            old_apps = executor.loader.project_state([before[0]]).apps
            company = old_apps.get_model("base", "Company").objects.create(
                company="Migration fixture"
            )
            tenant = old_apps.get_model("base", "Tenant").objects.create(
                company_id=company.pk, slug="migration-fixture", status="active"
            )
            ct = old_apps.get_model(
                "contenttypes", "ContentType"
            ).objects.get_or_create(app_label="base", model="tenant")[0]
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            new_apps = executor.loader.project_state(after).apps
            adopted = new_apps.get_model("saas", "Tenant").objects.get(pk=tenant.pk)
            self.assertEqual(adopted.company_id, company.pk)
            self.assertEqual(adopted.slug, "migration-fixture")
            self.assertEqual(adopted._meta.db_table, "base_tenant")
            self.assertEqual(
                new_apps.get_model("contenttypes", "ContentType")
                .objects.get(pk=ct.pk)
                .app_label,
                "saas",
            )
            self.assertNotIn("saas_tenant", connection.introspection.table_names())
            MigrationExecutor(connection).migrate(before)
            self.assertTrue(
                old_apps.get_model("base", "Tenant")
                .objects.filter(pk=tenant.pk)
                .exists()
            )
            self.assertEqual(
                old_apps.get_model("contenttypes", "ContentType")
                .objects.get(pk=ct.pk)
                .app_label,
                "base",
            )
        finally:
            MigrationExecutor(connection).migrate(after)
