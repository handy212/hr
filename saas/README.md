# SaaS platform

This app owns tenant administration and future platform features. HR business logic remains in Horilla's existing apps.

- `models.py`, `migrations/`: platform data and schema ownership
- `services.py`: tenant provisioning through HR adapters
- `forms.py`, `views.py`, `urls.py`: platform console
- `access.py`, `impersonation.py`: account lifecycle and support sessions
- `templates/saas/`, `static/saas/`: platform UI
- `menu.py`, `config.py`: host integration
- `tests/`: platform behavior, architecture boundaries and migration preservation

Use `from saas.models import Tenant`; do not add new tenant imports to `base.models`. Preserve the existing `/settings/tenants/` public URLs. The physical `base_tenant` table is retained for data compatibility; all new schema changes belong here.

See [the architecture decision and upstream integration register](../docs/architecture/saas-platform.md). Billing, plans, feature entitlements and platform notifications are future work, not implemented modules.

Validation:

```sh
python manage.py makemigrations --check --dry-run
python manage.py test saas.tests base.tests.test_company_scoping base.tests.test_saas_view_isolation horilla_api.tests.test_tenant_scoping base.tests.test_dashboard_roles offboarding
```
