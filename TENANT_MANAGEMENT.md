Current implementation ownership: [`saas`](saas/), with [architecture and upstream integration notes](docs/architecture/saas-platform.md). Apply `base/0019_move_tenant_to_saas` and `saas/0001_adopt_tenant` together via `python manage.py migrate`. Existing tenants retain the same database table and IDs. New migrations belong to `saas`.

# Managing tenants

## Enable the console

Apply the migrations in your normal application environment before serving the updated code (including `base.0018_tenant_deleted_at` if `0017` is already applied):

```bash
python manage.py migrate
```

For the Docker stack, use `docker compose exec web python manage.py migrate`.

Migration `base.0017_tenant` creates a platform account record for each existing company, with status **Active** and slug `company-<id>`. Existing users, passwords and permission assignments are preserved. Primary admin is initially unassigned for these legacy accounts; existing company role administration continues to work. The company relationship is protected against deletion through legacy company forms.

Sign in as an active **superuser** to land directly on `/settings/tenants/`. This is a standalone platform workspace with only tenant navigation and sign-out: it does not load the HR sidebar, check-in controls, company switcher or HR scripts/context processors. Platform operators need no employee profile. When two-factor authentication is enabled, verification is still required. Ordinary tenant users keep their HR landing pages. Staff status or normal model permissions do not grant platform access.

Use **Create tenant** for new accounts; companies added later through the legacy company form are not automatically registered as managed tenants.

## View, edit and delete

Each tenant row has **View**, **Edit** and **Delete** actions. View shows the account's company details, administrator login and status. Edit updates its company name, address, slug and trial/active/suspended status atomically. Duplicate slugs are rejected without partially saving company changes.

Delete requires a CSRF-protected POST and typing the exact tenant slug. It records `deleted_at`, suspends access and removes the account from the directory. Company, employee and audit records are retained, and the slug remains reserved. This is account deletion with retained data, not permanent erasure. Deleted records cannot be edited or reactivated by the ordinary status route. Platform operators can recover retained data administratively; a restore UI and permanent erasure workflow are separate work.

## Create an account

Enter the company details, unique slug, initial status, and initial admin's details. The email becomes the globally unique username. Enter a strong temporary password and share it securely yourself; provisioning sends no email and cannot display the password afterward. The normal first-web-login password-change flow applies.

Provisioning creates the company, its default settings, admin user, employee/work information, and company role assignment in one transaction. An error rolls back the entire operation. Both `COMPANY_SCOPING_STRICT` and `COMPANY_SCOPED_PERMISSIONS` must be enabled (the defaults).

The initial admin is a regular user, never staff or superuser. Their role grants view/add/change permissions for employees and basic organization settings (departments, job positions, job roles, work types, shifts and employee types), plus profile and work-information permissions. It does not grant company administration, platform operations, group management, SMTP administration, or every HR module. Add further company-scoped roles through the existing permissions interface as needed.

## Status and access

Search by company name or slug, filter by status, and use **Edit** to change status. Employee counts include active and inactive employee records.

- **Trial / Active:** authenticated access is allowed under the normal permission and isolation controls. Trial is a manually managed status; there is no expiry or billing automation yet.
- **Suspended:** authenticated session requests in that company and JWT requests (including already issued tokens) return 403. Existing user activation flags and data are preserved, so changing back to Active restores access. Platform superusers retain recovery access.
- An “All my companies” request is denied if its effective scope includes a suspended tenant. Users can still use the company switcher to select an active assigned company, or log out.

Suspension and retained-data deletion do **not** currently stop schedulers, device integrations, public recruitment pages or other unauthenticated entry points. Domain routing, subscriptions, plans/limits, permanent erasure and a customer billing console remain later work. Outstanding data-isolation findings in `SAAS_ROADMAP.md` still block a shared-customer launch.

## Implementation and verification

The acceptance contract is in `SPEC-tenants.md`. Tenant metadata is global control-plane data, managed by explicit platform-only views, with no ordinary generated CRUD permissions. Provisioning and status-change logs contain account/actor identifiers, never passwords.

Current CRUD validation: **75 targeted tests passed** using SQLite and an in-memory cache. These cover tenant CRUD, profile-free platform login, required two-factor verification, mail identity, CSRF, retained-data deletion and access denial, plus the existing SaaS and dashboard-role regressions. This also includes rollback, password/email validation, backfill, repeated provisioning/default seeding, suspension, multi-company switching, filters and pagination. Use the isolated test command in `SAAS_ROADMAP.md`, adding `saas.tests.test_tenant_management` and `base.tests.test_dashboard_roles` to its labels. Migration drift and Ruff checks passed.

Revalidated on 2026-10-20: Chromium verified default login landing, the isolated platform shell, search, create, detail, edit and confirmed deletion at desktop (1440px) and mobile (390px) sizes with zero JavaScript page errors. The platform engine loads only its own stylesheet and minimal authentication/message context processors. Screenshots and logs are in `~/.cache/hr-tenant-validation` (`platform-tenants-desktop.png`, `platform-create-mobile.png` and `browser-crud.log`).

Additional compatibility testing ran 36 existing tests. The 11 failures in `horilla_api.tests.test_api_write_target_scoping` (3 failures, 8 errors) reproduce with the new suspension middleware removed and the JWT suspension helper disabled; the failing cases are identical. They involve strict-context fixture reads and permission expectations and remain unresolved. The other 25 compatibility checks passed. This is not a claim that the full application suite is green.

The configured application database has not been migrated during development. Validation uses isolated databases; the resumed verification stores its database, logs and browser screenshots under `~/.cache/hr-tenant-validation` so they survive clearing `/tmp`.

### Tenant impersonation

Platform operators can use **Impersonate admin** on the tenant directory or details page. The session becomes the assigned administrator, with that user's existing company permissions. For a legacy tenant with no administrator, use **Edit → Primary admin** to choose an active employee user belonging to that company. Superusers and users from other companies cannot be assigned here. This does not grant new permissions to the chosen user.

The HR workspace displays an impersonation banner and a CSRF-protected **Return to platform** button. Starting and stopping require POST. Start requires platform authorization (and verified OTP when enabled), strict company scoping, and company-scoped permissions. Suspended/deleted tenants are blocked. Original operator activity, superuser status and password session hash are rechecked on each request; revocation logs the session out. Tenant suspension or administrator reassignment ends impersonation. Company switching and the web password-change page are blocked while impersonating. The new-employee password-change gate is skipped only during the support session; the user's flag is preserved. Logging out ends the entire session. Start and stop logs include operator, tenant and target identifiers.

No database migration is needed for impersonation. Reload web workers after updating Python code so the routes and middleware load together.


### Extraction validation (2026-10-20)

84 targeted platform, boundary, company-scope, API, dashboard and offboarding regression tests pass after extraction. The separate migration regression exercises upgrade and rollback while retaining tenant rows and content-type identity. Django reports no migration drift. Browser checks pass for tenant login/CRUD and impersonation/return. The local PostgreSQL tenant-row fingerprint is identical before and after applying the ownership migration; static assets were collected and web/scheduler processes reloaded.
