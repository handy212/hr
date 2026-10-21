# Horilla → SaaS Conversion Roadmap

**Goal:** Convert Horilla HRMS (Django 5.2) into a fully multi-tenant SaaS product.
**Strategy:** Shared-schema, row-level tenancy built on the existing `HorillaCompanyManager` + ContextVar architecture. Schema-per-tenant (django-tenants) is explicitly rejected — it would discard existing scoping work and conflicts with the SQLite default, ~45 migration directories, and the audit/notification stack.

**Tenant unit:** `base.models.Company` is the de-facto tenant and stays that way; a new tenant/account layer is added on top of it.

## Tenant management checkpoint — 2026-10-20

Product correction: the operator console now uses a standalone platform layout, and superuser login/root requests lead directly to tenants. Its UI contains no HR sidebar or check-in controls. Tenant account CRUD now includes detail and edit pages plus a slug-confirmed retained-data delete operation (`base.0018_tenant_deleted_at`). Deleted accounts are hidden and blocked from authenticated access; company/employee/history records are retained. Ordinary customer users keep their HR workspace. See the management guide for the exact deletion and access boundaries.

The first platform management slice is implemented. After applying `base.0017_tenant`, an active platform superuser can open **Settings → Organization → Tenants** (`/settings/tenants/`) to list/search accounts, create a company and its initial HR admin atomically, and change trial/active/suspended status. Existing companies are backfilled as active accounts without changing users or permissions. Tenant admins remain regular users with an explicit company-scoped HR role.

Suspension is enforced on authenticated session and JWT requests; platform recovery and company switching remain available. Background jobs, devices and public pages are outside this first suspension policy. Repeated provisioning also fixes strict-mode attendance-default seeding, preserving existing duplicate configurations rather than deleting them.

Current model-manager inventory (2026-10-21): **244 scoped / 0 unscoped / 42 classified global**, across 286 models. This manager-level count does not cover `.unscoped()`/`_base_manager`/raw-query bypasses or prove every global model is safe to expose. `Tenant` is intentionally global control-plane metadata with explicit platform-only access. The Phase 1 isolation tasks below still block shared-customer launch. Domain routing, plans, billing, ghself-service signup and deletion remain pending.

See [TENANT_MANAGEMENT.md](TENANT_MANAGEMENT.md) for operation, migration and verification details, and [SPEC-tenants.md](SPEC-tenants.md) for the implemented acceptance contract. The configured application database has not been migrated during development.

## SaaS review checkpoint — 2026-10-21

- **Report-filter cache isolation is implemented.** `build_filter_options` uses an authorization-scoped cache key; regression tests cover selected-company scope, `all` scope with distinct assignments, and missing context. Company filter options are limited to the effective company scope.
- **SMTP ownership is implemented.** Tenant-owned configurations cannot become globally primary. Mail resolution prefers the selected company's configuration and otherwise falls back only to a platform-owned configuration; tenant users cannot access the legacy SMTP management endpoints.
- Verification: `saas.tests` passed **61 tests**, including `saas.tests.test_report_isolation` and `saas.tests.test_mail_ownership`. The fresh manager audit reports **244 scoped / 0 unscoped / 42 global / 286 total**.
- These results do not satisfy Phase 1 exit criteria. Remaining work includes request-path bypasses, nullable/shared-row ownership, global history/notification access paths, broader cache namespacing, and media/template isolation.

## Import isolation checkpoint — 2026-10-22

- **Employee imports now enforce tenant boundaries.** Generic related-record resolution uses each model's normal manager instead of `.entire()`. Employee company references are additionally limited to `effective_company_ids()`; unavailable or out-of-scope references reject the row, and imports cannot create Company records.
- Import endpoints check add permission against the current request and require change permission for rows with update references; denied requests receive `403`.
- Regression coverage verifies that imports reject another company's assignment on create and update, accept an allowed company, and deny callers without add/change permission.
- Verification: the three focused employee import tests, `saas.tests`, and `horilla_views.tests` passed together (**72 tests**) with SQLite and `LocMemCache`. A broader 87-test run including all `employee.tests` reported six failures/errors in archive, company-work-info, and last-superuser-delete tests; those remain open.
- This closes one confirmed request-path bypass; the remaining Phase 1 isolation audit and exit criteria are still outstanding.

## Attendance settings isolation checkpoint — 2026-10-22

- **The Time Runner “all companies” action now respects tenant scope.** Regular users update settings only within `effective_company_ids()`; an empty scope is rejected with `403`. The shared setting and unrelated tenant rows are not changed by customer users. Platform superusers retain the existing global behavior.
- Regression coverage verifies updates for multiple assigned companies, rejection with no company scope, and global behavior for a superuser employee account.
- Verification: `attendance.tests.test_timerunner_company_scope` passed (**3 tests**). This closes one more request-path bypass; it does not satisfy Phase 1 exit criteria.

## Review checkpoint — 2026-10-19

The conversion remains in Phases 0–1. Tenant provisioning, billing, and self-service signup are still pending. The counts below supersede the older progress snapshots in this document; manager coverage alone does not prove isolation.

- Audit before this slice: **186 scoped / 61 potential gaps / 36 classified global**, across 283 installed models.
- **Offboarding stage isolation:** reproduced a move from a selected-company employee into another company's stage. `OffboardingStage` now uses `HorillaCompanyManager("offboarding_id__company_id")`, protecting stage choices and request lookups. Removed its scoping allowlist entry. New two-company tests cover stage reads, missing context, and the stage-change request. Coverage after this slice: **187 scoped / 60 potential gaps / 36 classified global**.
- **JWT strict-mode regression:** existing API isolation tests reproduced valid assigned users receiving 403 responses. Resolving the authenticated user's company used a scoped work-information query before company context existed. That bootstrap query now explicitly uses `.unscoped()` and restricts the lookup to the authenticated user's ID. Tests explicitly enable strict mode and also cover a conflicting pre-existing company context.
- **Employee-view audit correction:** the legacy employee detail/update and profile lookups already use scoped managers and have cross-company regression coverage. The older bypass triage note below is historical.

### Next isolation tasks, in priority order

1. **Audit request-path bypasses.** Catalog `.unscoped()`/`.entire()`, `_base_manager`, raw SQL, and direct SQL access. For each runtime path, confirm ownership and add cross-company regression coverage, prioritizing APIs, exports, and reports.
2. **Review nullable/shared rows and global models.** The manager deliberately treats some nullable-company rows as shared. Make model-specific ownership decisions and review access paths for notification, history, and configuration tables.
3. **Complete the broader isolation work.** Audit cache keys beyond report filters, media paths, DB-template ownership, uniqueness constraints, and third-party history/notification behavior.
4. After Phase 1 isolation acceptance criteria pass, continue the remaining Phase 2 identity and per-tenant configuration work.

### Validation

- Existing baseline: 17 company-scoping and SaaS-view tests passed before implementation.
- Reproduction: all three new stage tests failed before the fix, including an actual cross-company stage mutation. Existing JWT isolation tests failed with three failures and one error before the resolver fix.
- Combined regression run: **39 tests passed**, covering the full `offboarding` suite, `base.tests.test_company_scoping`, `base.tests.test_saas_view_isolation`, and `horilla_api.tests.test_tenant_scoping`.
- `DATABASE_URL=sqlite:////tmp/hr-saas-audit.sqlite3 DJANGO_LOG_LEVEL=ERROR .venv/bin/python manage.py makemigrations --check --dry-run`: no changes detected. Syntax compilation and `git diff --check` passed. Ruff was unavailable in this environment.
- Environment limits: the default-cache run had seven Redis connection errors because Docker hostname `redis:6379` is unavailable from this shell. The successful combined run explicitly used SQLite and `LocMemCache`; it does not validate PostgreSQL/Redis behavior or the full application suite. Existing startup-query and cache-key warnings remain.

Reproduce the combined run from the repository root (the test database is isolated and retained to avoid repeating all migrations):

```bash
DATABASE_URL=sqlite:////tmp/hr-saas-validation-app.sqlite3 DJANGO_LOG_LEVEL=ERROR .venv/bin/python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'horilla.settings')
from django.conf import settings
settings.DATABASES['default']['TEST'] = {'NAME': '/tmp/hr-saas-validation-test.sqlite3'}
settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py', 'test', 'offboarding', 'base.tests.test_company_scoping', 'base.tests.test_saas_view_isolation', 'horilla_api.tests.test_tenant_scoping', '--noinput', '--keepdb', '--verbosity=1'])
PY
```

---

## Phase 0 — Foundations & Audit (Weeks 1–3)

**Objective:** Know exactly what leaks today, and stop new leaks from being introduced.

- [x] **Full model coverage audit.** `python manage.py audit_company_scoping` (`base/company_scoping.py`) walks all 283 models. Baseline 2026-10-14: **143 scoped, 127 unscoped gaps, 13 intentionally global**. Definite gaps (unscoped but have a company FK): `facedetection.facedetection`, `geofencing.geofencing`, `horilla_theme.companytheme`, `horilla_audit.historytrackingfields`, `pms.historicalkeyresult`, `pms.historicalobjective`, `base.dynamicemailconfiguration`, `base.companygroupassignment`, `base.companylanguagesetting`, `base.defaultexportpermission`, `base.integrationapps`, `base.setupchecklistdismissal`. Also found: broken filter path on `recruitment.questionordering` (`recruitment_ids__company_id` — field doesn't exist).
- [x] **Fail-closed scoping.** `COMPANY_SCOPING_STRICT` env flag added (`horilla/settings/base.py`); when True, `HorillaCompanyManager` returns an empty queryset with no company context (and for "all" without request assignment) instead of unfiltered. Default changed to True on 2026-10-15; coverage remains incomplete. Explicit escape hatch: `.unscoped()` (alias of `.entire()`) on the manager.
- [x] **Extend the testkit / CI gate.** `base/tests/test_company_scoping.py`: strict-mode behavior tests + `test_no_new_unscoped_models` fails CI when any model outside the `KNOWN_UNSCOPED` allowlist (127-entry snapshot) is unscoped. The allowlist must only shrink.
- [ ] **Audit bypass paths.** Catalog uses of `.entire()`, `_base_manager`, `raw()`, and direct SQL; wrap or flag each.
  - Initial runtime sweep (2026-10-18): direct SQL in `horilla_views/views.py` rechecks submitted IDs through `model.objects`; migration/demo SQL is operational. Scoped manager reads now replace bypasses in the employee profile, recruitment pipeline shell, and legacy offboarding dashboard/list views, with two-company regression tests for the profile, pipeline, and chart. Follow-up 2026-10-19: the employee detail/update and profile paths already use scoped lookups and have cross-company tests. Continue cataloging other request-path bypasses; this remains a triage list, not a completed audit.
- [ ] **Baseline threat model.** Document every place cross-tenant data could surface (API, exports, reports, mail previews, notifications, admin).
  - Mail configuration is a concrete control-plane risk: `base/views.py:replace_primary_mail` previously allowed any logged-in user to replace the global primary mail server; it now requires the change permission. `DynamicEmailConfiguration.save()` still clears the globally primary flag across companies. Define platform versus tenant mail ownership before SaaS rollout.

**Exit criteria:** Coverage inventory published; fail-closed manager merged behind a flag; CI scoping test running.

### Phase 0 threat-model baseline (2026-10-18)

| Surface | Current control | Isolation work still required |
|---|---|---|
| Session web views | Company ContextVar and scoped managers | Audit remaining `.entire()` / `_base_manager` request paths; test cross-company object IDs. |
| JWT API | `TenantScopedJWTAuthentication` sets company context or rejects an unassigned user | Test every viewset and serializer relation with two tenants; define tenant switching for users in multiple companies. |
| Exports and reports | `has_export_access` gates the shared export helper; raw SQL export in `horilla_views/views.py` rechecks IDs through a scoped manager | Check every export/report entry point, including relation traversal and stored report results. |
| Mail and notifications | Mail replacement now requires change permission; notification API starts from the authenticated user's notifications | Separate platform and tenant SMTP ownership; verify notification creation and read paths never expose another tenant's content. |
| History, admin, and files | Some history and third-party models remain global; Django admin registers tenant models | Define tenant filters for history/admin and tenant-owned upload paths before shared-instance deployment. |

This is an entry-point inventory, not a completed security review. Phase 1 exit criteria remain unmet while unscoped models and bypass paths remain.

---

## Phase 1 — Complete Data Isolation (Weeks 3–8)

**Objective:** No query path can return another tenant's data.

- [~] Scope all remaining tenant-owned models. **Progress 2026-10-14: 153/283 scoped (was 143).** Done: broken `recruitment.questionordering` path fixed (`recruitment_id__company_id`); scoped `facedetection.facedetection`, `geofencing.geofencing`, `horilla_theme.companytheme`, `horilla_audit.historytrackingfields`, `base.dynamicemailconfiguration`, `base.companylanguagesetting`, `base.defaultexportpermission`, `base.integrationapps`, `base.setupchecklistdismissal`. Deliberately deferred: `base.companygroupassignment` (CompanyScopedBackend resolves assignments across all companies at `base/auth_backends.py:80` — scoping breaks "all companies" permission resolution; needs a dedicated design), all simple-history tables (written without request context; scope via parent in a separate pass). Remaining unscoped: 117, mostly comments/attachments/history and view-state tables.
- [x] **Strict-mode blockers fixed; `COMPANY_SCOPING_STRICT` now defaults to True (2026-10-15).** (a) `base/backends.py` `get_dynamic_email_config()` — company-context config preferred, unscoped global-primary fallback for scheduler mail; (b) `base/models.py` `DynamicEmailConfiguration.save()` primary-bookkeeping via `.unscoped()`; (c) `base/views.py` `replace_primary_mail` switches the primary without deleting a mail configuration. Suite-wide strict fallout also fixed: Company post-save seeders (`base/signals.py` AnnouncementExpire/DefaultExportPermission, `create_shift_days`), `employee/models.py` `bonus_post_save`, `horilla_audit` `seed_default_row`, export-access readers (`base/methods.py`, `base/context_processors.py`), and testkit `make_employee` now use `.unscoped()` where cross-company access is intended. Full `base` suite: identical results with strict ON vs OFF (162 tests; only pre-existing environmental redis/observability failures). Remaining known strict hazard: `base.companygroupassignment` still unscoped by design (CompanyScopedBackend resolves across companies — needs a dedicated design before scoping).
- [ ] Add `unique_together` constraints including `company` where uniqueness is currently global (codes, badges, work types, etc.).
- [ ] Scope or replace global third-party tables:
  - `django-notifications` — filter by recipient's company or add tenant FK via custom model.
  - `django-auditlog` / `simple-history` — capture company context on write, filter on read.
  - `django-axes` — acceptable global for lockout, but confirm username strategy (below).
- [ ] **Cache namespacing.** Prefix all cache keys with tenant id (13 files use `cache.get/set`); replace the global `KEY_PREFIX: "horilla"` assumption in `horilla/settings/base.py`.
- [ ] **Media isolation.** Move uploads to per-tenant paths (`tenant/<id>/...`) in `horilla/models.py` upload-to logic; configure django-storages/S3 for production.
- [ ] **DB templates.** Partition `horilla_dbtemplate` records per tenant.

**Exit criteria:** Cross-tenant isolation test suite green; manual pen-test checklist on API + exports + reports shows no leaks.

---

## Phase 2 — Tenant Layer & Identity (Weeks 8–12)

**Objective:** Real tenant provisioning and a coherent multi-tenant login story.

- [~] **Tenant model.** Implemented: `Tenant` 1:1 with `Company`, unique slug, trial/active/suspended status, primary admin and timestamps. Pending: domains, plan FK and subdomain routing alongside session selection.
- [~] **Provisioning pipeline.** Implemented: atomic service and platform form create the company, default settings, initial admin and company-scoped HR role. Pending: demo-data option and broader provisioning automation.
- [ ] **User identity.** Users stay global (`HorillaUser`), but define:
  - Login flow with tenant resolution (email + subdomain, or tenant picker post-login).
  - Username/email uniqueness policy (per-tenant vs global — recommend global email, per-tenant display).
  - Fix `TenantScopedJWTAuthentication` to resolve any tenant the user belongs to, not just the default company.
- [ ] **Per-tenant configuration.** Convert global singletons to per-tenant rows with an inheritance layer (tenant override → platform default): `DynamicEmailConfiguration`, `LDAPSettings`, backup settings, `PayrollGeneralSetting`, `RecruitmentGeneralSetting`, etc. Global SMTP becomes the platform fallback, not the only option.
- [ ] **Scheduler multi-tenancy.** Rework `horilla/scheduling.py` / `base/scheduler.py` jobs to iterate active tenants and set the company ContextVar per tenant. Evaluate moving to Celery if job volume grows.

**Exit criteria:** New tenant provisioned end-to-end via one command; two tenants on one instance fully isolated with per-tenant mail settings; scheduler jobs run per-tenant.

---

## Phase 3 — SaaS Product Layer (Weeks 12–18)

**Objective:** Sell it.

- [ ] **Billing.** Stripe integration: customers, subscriptions, plan tiers (by employee count and/or module bundle), proration, dunning, invoices, webhooks → tenant status sync. Nothing exists today; this is greenfield.
- [ ] **Plans & feature gating.** Plan ↔ module mapping (payroll, recruitment, biometric, API access, etc.); enforcement decorator/middleware; usage metering (employees, storage, API calls).
- [ ] **Self-serve signup.** Registration → tenant provisioning → trial; email verification; onboarding checklist (leverage `horilla_tour`).
- [ ] **Tenant admin console.** Manage users, plan, billing, domains, data export; suspend/reactivate.
- [~] **Platform admin console.** Implemented: superuser-only tenant directory, search, status filters, employee counts, provisioning and status changes. Pending: health, metered usage, MRR and impersonation with audit.
- [~] **Lifecycle ops.** Implemented: suspension denies authenticated web/JWT access with platform recovery. Pending: background/device/public-route policy, deletion/export and per-tenant backup/restore (`horilla_backup` extension). Current suspension denies reads as well as writes.

**Exit criteria:** A stranger can sign up, get a trial tenant, invite employees, hit a plan limit, upgrade via Stripe, and be billed — without staff involvement.

---

## Phase 4 — Hardening & Launch (Weeks 18–22)

- [ ] **Security review / pentest** focused on cross-tenant access, JWT tenant switching, exports, and API.
- [ ] **Performance.** Shared-schema row counts will grow fast — index audit on company FK paths, query-plan review of the scoped manager on large tables (attendance, payroll), connection pooling (pgbouncer), Postgres-only production stance (SQLite remains dev-only).
- [ ] **Observability.** Per-tenant logging/metrics tags, Sentry with tenant context, scheduler job health, billing webhook alerts.
- [ ] **Deployment.** Containerize per-tenant-neutral app tier (see `docker/`), infrastructure-as-code, blue/green or rolling deploys, migration strategy for large shared schema (expand/contract migrations).
- [ ] **Docs & legal.** Tenant-facing docs, DPA/privacy policy, SLA, backup/DR runbook.
- [ ] **Beta program.** Migrate 2–3 existing single-tenant customers into shared instance as validation.

**Exit criteria:** Pentest findings resolved; load test at target tenant count passes; DR drill completed; beta tenants live.

---

## Ongoing workstreams (all phases)

| Stream | Notes |
|---|---|
| Isolation regression tests | CI gate from Phase 0 runs forever; every new model must pass scoping tests |
| Migration discipline | Shared schema = one migration set; avoid per-tenant migration tooling entirely |
| Upstream sync strategy | If still tracking upstream Horilla, reconcile scoping conflicts continuously |
| API parity | Every web feature gets tenant-scoped API coverage via `horilla_api` |

## Key risks

1. **Fail-open legacy paths** — the single biggest leak risk; mitigated by Phase 0/1 CI gates.
   The scoped manager also includes `company_id IS NULL` rows for a selected company; confirm every such row is deliberately shared before claiming isolation.
2. **Scheduler scale** — single-process apscheduler iterating many tenants will bottleneck; budget a Celery migration if tenant count grows.
3. **Payroll/audit correctness** — scoping payroll wrong is a legal problem, not just a bug; extra review + staging validation.
4. **Global settings refactor** — touches mail delivery for everyone; do it with dual-read fallbacks.

## Estimated effort

~5–6 months for a small team (2–3 engineers) to beta; Phases 0–1 are the non-negotiable foundation, Phases 2–3 can overlap with a second engineer on billing.


### Platform extraction — 2026-10-20

Tenant management and impersonation now belong to the `saas` app, including model ownership, migrations going forward, routes, templates, assets, menu and tests. Existing `base_tenant` data is adopted without table DDL. See `docs/architecture/saas-platform.md` for the retained core hooks and upstream merge process. Billing, plans, entitlements and platform notifications remain unimplemented; future platform work belongs here rather than in HR modules. The existing tenant-isolation gaps and production-readiness limitations above remain open.
