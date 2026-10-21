"""Retire the old model state only; saas adopts the same physical table."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("base", "0018_tenant_deleted_at")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name="Tenant")],
        ),
    ]
