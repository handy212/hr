# SaaS platform

This app owns tenant administration, billing and plan feature access. HR business logic remains in Horilla's existing apps.

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

Tenant detail → **Billing** lets platform operators assign a published plan and a
0–365 day trial. Enrollment is explicit; tenants without subscriptions retain their
existing access. The primary administrator can open **Settings → Subscription → Billing**
or `/billing/`. Other employees cannot view or change billing.

Checkout supports one-time renewal and optional automatic renewal. Prices and billing
identity come from server records. Confirmed payments extend access exactly once;
manual suspension is preserved. Canceling automatic renewal keeps paid access through
its expiry. In-app notices cover payment success/failure, renewal setup and expiry;
the existing single scheduler generates expiry notices hourly.

Configure `SAAS_PUBLIC_URL` as the public HTTPS origin and register
`<origin>/billing/paystack/webhook/` in Paystack. The callback is
`<origin>/billing/paystack/callback/`; returning from checkout is not payment proof.
Use **Check payment** to verify a callback manually, or let the signed webhook verify
it. Gateway errors do not grant access. An uncertain setup must be reconciled using
the existing provider code, rather than creating a duplicate subscription.

Plans select optional HR modules in **Included modules**. Employee records, organization,
authentication and billing remain available. Existing plans and unenrolled tenants
keep all current modules. Publication freezes the selected modules; create a new
plan version to change them. See [the feature contract](../docs/architecture/saas-entitlements.md).

Plan switching/proration, restarting canceled automatic renewal,
email/SMS billing notices and automatic refund/dispute adjustments are not implemented.
Canceled accounts can renew manually. Creating or publishing a catalog plan does not
charge or enroll a tenant. Validate test-mode end-to-end payments with your own Paystack
account before enabling live collections; automated gateway tests use mocked responses.
See [the billing contract](../docs/architecture/saas-billing.md).

Validation:

```sh
python manage.py makemigrations --check --dry-run
python manage.py test saas.tests base.tests.test_company_scoping base.tests.test_saas_view_isolation horilla_api.tests.test_tenant_scoping base.tests.test_dashboard_roles offboarding
```
