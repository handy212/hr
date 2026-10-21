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

See [the architecture decision and upstream integration register](../docs/architecture/saas-platform.md). The platform plan catalog is available at `/settings/plans/` (Plans in the platform navigation).
Operators create, edit and archive local drafts and explicitly publish to Paystack.
Set `PAYSTACK_SECRET_KEY` in the server environment or server `.env` to publish;
restart the web process after changing it. No key is required for drafts.
A key determines whether plans are published in Paystack test or live mode. A published
price cannot be edited; create a new plan version instead. If publication is uncertain,
verify and link the existing `PLN_` code from the Paystack dashboard.

Subscriptions, checkout, feature entitlements and platform notifications remain future
work. Creating or publishing a catalog plan does not charge or enroll a tenant.
See [the billing contract](../docs/architecture/saas-billing.md).

Validation:

```sh
python manage.py makemigrations --check --dry-run
python manage.py test saas.tests base.tests.test_company_scoping base.tests.test_saas_view_isolation horilla_api.tests.test_tenant_scoping base.tests.test_dashboard_roles offboarding
```
