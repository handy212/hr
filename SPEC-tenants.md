# Tenant management foundation

## Updated product requirement — 2026-10-20

The platform operator must see a dedicated tenant workspace after login, without the HR sidebar, check-in timer, employee navigation or company-settings shell. Superuser login and the root URL lead to the tenant directory; platform accounts need no Employee record. Customer users retain their normal HR landing behavior.

The platform workspace provides account CRUD: creation, detail, editing company details/slug/status, and a POST+CSRF deletion confirmation requiring the exact slug. Deletion is explicitly a retained-data deletion: set `deleted_at`, suspend the account, remove it from the directory and deny authenticated access. Retain company/employee/history data and reserve the slug; permanent data erasure is separate. Deleted accounts cannot be edited or reactivated through the ordinary management routes. No real customer record is deleted during implementation or verification.

Render this workspace using a minimal named template engine so it does not load HR context processors or scripts. Its only product navigation is Tenants plus account sign-out. The former settings link remains a compatibility entry point.

Approved scope: the user's “let's do it” follows the proposal for a tenant record, provisioning service, and platform tenant console. Build this as one vertical slice: model → provisioning/status policy → platform UI.

## Objective and contract

- Each Company has one platform-owned Tenant record: unique lowercase slug, trial/active/suspended status, primary admin, timestamps, creator.
- Existing companies become active tenants in an additive migration with deterministic `company-<id>` slugs. No existing users or permissions change.
- Only active platform superusers may list, create, or update tenants. Staff and tenant admins cannot access these endpoints, even with ordinary model permissions.
- Provisioning atomically creates a company, seeded defaults, a regular user, employee/work information, and a company-assigned HR administration group. Admin login uses globally unique email; passwords use configured validation and hashing and must be changed on first login. No email is sent.
- Initial HR permissions are an explicit allowlist for employee and organization maintenance. Tenant admins never receive superuser, staff, global-company, group-management, or mail-server permissions. Strict scoping and company-scoped permissions must be enabled for provisioning.
- Platform console lists tenants with employee counts, searches by name/slug, paginates, provisions tenants, and updates status. Company details continue to use existing company settings.
- Suspension blocks authenticated web and JWT requests in the selected company; platform superusers remain able to recover tenants. “All my companies” requests are denied if their effective scope includes a suspended tenant; selecting an active company works. Logout remains available. This does not yet stop background jobs, device credentials, public recruitment routes, or introduce subdomain routing.
- Billing, limits, domains, impersonation, tenant deletion, and customer self-service are later capabilities. The unresolved Phase 1 isolation findings still block shared-customer launch.

## Implementation conventions

Django 5.2, existing server-rendered templates/CSS and session authentication. Model in `saas/models.py`; focused forms, services, views and access policy in `saas/`; templates in `saas/templates/saas/tenants/`; tests in `saas/tests/test_tenant_management.py`. No new runtime dependencies. Tenant metadata is intentionally global control-plane data and has explicit platform authorization; no generic tenant model CRUD is exposed.

Use `transaction.atomic()` around provisioning, `full_clean()`/form validation, explicit scoped permission grants, POST+CSRF for mutations, escaped templates, semantic labels and responsive tables. Record changes without logging credentials.

## Verification and implementation order

1. Add regression/acceptance tests; confirm they fail before implementation.
2. Add model and reversible schema migration plus legacy-company backfill.
3. Add atomic provisioning, restricted roles, suspension policy and platform endpoints.
4. Add the settings navigation entry and responsive tenant list/create/status pages.
5. Run tenant management tests plus the existing 39 SaaS regressions with SQLite/LocMemCache; check migration drift, system checks and syntax. Test rollback on failure, duplicate inputs, password validation, authorization/CSRF, normal tenant admin access, suspended web/JWT denial, platform recovery and legacy backfill. Attempt browser verification when a browser is available; document any limit.

Use the isolated retained test database command in `SAAS_ROADMAP.md`, adding `saas.tests.test_tenant_management` to its test labels. Run `.venv/bin/python manage.py makemigrations --check --dry-run` against a scratch SQLite database. Do not migrate the user's configured database or deploy during implementation.


## Platform ownership (2026-10-20)

Tenant CRUD, impersonation, lifecycle policy, model ownership, forms, services, URLs, templates, static assets, menu registration and tests belong to the `saas` Django app. Keep the existing tenant table, IDs and public URLs through a state-only ownership migration. Move its content type without changing the ID. Preserve legacy migration history. Record necessary core hooks and the upstream update workflow in `docs/architecture/saas-platform.md`. Billing, subscriptions, entitlements and platform notifications are future platform features, not changes to HR business logic in this extraction.
