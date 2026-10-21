# SaaS platform ownership

Accepted 2026-10-20.

Tenant administration previously lived inside Horilla's `base` app. That made every platform feature a potential conflict with upstream HR changes. SaaS behavior now belongs in `saas`; Horilla remains the HR application. This is an in-process Django extension, not an independent service and not a claim that the HR fork has no modifications.

## Ownership

`saas/models.py` owns tenant metadata; `services.py` provisions company accounts; `forms.py` and `views.py` implement tenant CRUD. `access.py` owns account lifecycle enforcement and `impersonation.py` owns support sessions. URLs, settings menu registration, platform template-engine configuration, templates, namespaced static assets and platform tests also live in this app.

Billing, plans, subscriptions, feature entitlements, platform notifications and onboarding must be implemented inside this app (or dedicated platform sub-apps), with their own migrations and tests. They are not implemented by this move. HR notifications and platform billing notifications have separate ownership. Do not insert provider callbacks, plan fields or payment rules into payroll, attendance, employee models or the HR navigation.

The platform can call HR operations through services. Platform rules must not be enforced merely by hiding UI. If a plan eventually restricts HR functionality, introduce a tested authorization hook at that operation's boundary. Do not scatter billing checks across HR views. Do not monkey-patch upstream classes or rely on startup-time model mutation.

## Database adoption

The old `base/0017` and `0018` migrations are immutable history. `base/0019_move_tenant_to_saas` removes only the old Django model state. `saas/0001_adopt_tenant` adopts that state with `db_table="base_tenant"` and no table DDL. Existing tenant IDs, slugs, company/admin links, status and deleted timestamps remain in place. The content-type app label moves while retaining its ID, preserving existing generic references and permissions attached to it. Conflicting content types stop migration for investigation rather than deleting references.

All future tenant schema migrations belong to `saas`. The legacy table name is deliberate compatibility, not core ownership. Do not rename it solely for appearance. Both upgrade and rollback must preserve tenant records; migration regression tests exercise both directions. Deploy both migrations together using ordinary `migrate`, never `--fake`. A fresh installation still runs historical company backfill before adopting the table.

## Core integration register

| Integration | Reason to retain | Review after upstream updates |
| --- | --- | --- |
| `horilla/settings/base.py` | Register SaaS app, platform template engine and middleware | Middleware order: session/auth → impersonation → company scope → tenant access |
| `horilla/urls.py` | Include `saas.urls` | Public names and `/settings/tenants/` URLs must remain stable; SaaS also owns `/settings/plans/` |
| `base/views.py` | Root login/home routing, platform OTP rendering, employee-free operator login | Tenant users retain normal HR landing and authentication |
| `base/middleware.py` | Skip company context for platform routes; support-session password gate integration | Scope cleanup and customer password-change behavior |
| Both `templates/index.html` and `horilla_theme/templates/index.html` | Include platform-owned return banner | Visible return action in both HR layouts |
| `horilla_api/authentication.py` | Call SaaS lifecycle policy after resolving JWT company scope | Suspended/deleted tenants remain blocked |
| `base/checks.py`, `base/company_scoping.py` | Classify SaaS tenant/plan metadata as platform-global with explicit authorization | No blanket exclusions of other tenant data |
| `base/backends.py` | Email sender fallback for operators without an employee record | OTP email still works for operators |
| `horilla_theme/templates/settings.html` | Full-page settings-menu extension hook | Tenant console does not become an embedded HR page |
| Historical `base` tenant migrations | Preserve deployed migration history | Never rewrite or delete applied migrations |

Existing company managers, scoped permissions, JWT context bootstrap, attendance default creation and offboarding stage isolation remain core tenancy/security integration. They predate this ownership move and cannot safely be removed merely to reduce the diff. Further isolation work must preserve these protections. This extraction does not resolve the remaining scoping gaps recorded in `SAAS_ROADMAP.md`.

Generic history views are also a tenancy boundary: they resolve base objects through company-scoped managers, require model-level view/change permissions, and bind history reverts to the authorized object.

Generic import views are a tenancy boundary as well: related records must resolve through authorized querysets, missing or out-of-scope references fail closed, and create/update permissions are checked against the current request. Employee imports may only resolve companies in the effective company scope and never provision Company records.

The attendance Time Runner setting action is also tenant-scoped: an “all companies” selection updates only the authenticated user's effective company IDs, while a platform superuser retains the global operation. An empty customer scope is denied, and the shared setting is not changed by customer requests.

## Upstream update procedure

The configured `upstream` remote is `https://github.com/horilla/horilla-hr.git`; `origin` is this product's fork. Fetch and inspect the intended release before merging on a temporary integration branch. Never pull upstream directly into a running environment. Resolve core changes with the register above; retain the platform app and migration graph. Do not blindly replace local authentication or tenancy code with upstream versions.

Before release: review the core diff, run `makemigrations --check --dry-run`, run the SaaS boundary/migration/tenant tests plus company scope, API scope, dashboard routing and offboarding tests, and browser-test platform login, tenant CRUD, impersonation and return. Validate a database upgrade on a backup copy. Deploy migrations, collect static files and reload web/scheduler processes together. A separate app reduces merge conflicts; it does not guarantee conflict-free upgrades.
