# Horilla → SaaS Conversion Roadmap

**Goal:** Convert Horilla HRMS (Django 5.2) into a fully multi-tenant SaaS product.
**Strategy:** Shared-schema, row-level tenancy built on the existing `HorillaCompanyManager` + ContextVar architecture. Schema-per-tenant (django-tenants) is explicitly rejected — it would discard existing scoping work and conflicts with the SQLite default, ~45 migration directories, and the audit/notification stack.

**Tenant unit:** `base.models.Company` is the de-facto tenant and stays that way; a new tenant/account layer is added on top of it.

---

## Phase 0 — Foundations & Audit (Weeks 1–3)

**Objective:** Know exactly what leaks today, and stop new leaks from being introduced.

- [x] **Full model coverage audit.** `python manage.py audit_company_scoping` (`base/company_scoping.py`) walks all 283 models. Baseline 2026-10-14: **143 scoped, 127 unscoped gaps, 13 intentionally global**. Definite gaps (unscoped but have a company FK): `facedetection.facedetection`, `geofencing.geofencing`, `horilla_theme.companytheme`, `horilla_audit.historytrackingfields`, `pms.historicalkeyresult`, `pms.historicalobjective`, `base.dynamicemailconfiguration`, `base.companygroupassignment`, `base.companylanguagesetting`, `base.defaultexportpermission`, `base.integrationapps`, `base.setupchecklistdismissal`. Also found: broken filter path on `recruitment.questionordering` (`recruitment_ids__company_id` — field doesn't exist).
- [x] **Fail-closed scoping.** `COMPANY_SCOPING_STRICT` env flag added (`horilla/settings/base.py`); when True, `HorillaCompanyManager` returns an empty queryset with no company context (and for "all" without request assignment) instead of unfiltered. Default False until coverage is complete. Explicit escape hatch: `.unscoped()` (alias of `.entire()`) on the manager.
- [x] **Extend the testkit / CI gate.** `base/tests/test_company_scoping.py`: strict-mode behavior tests + `test_no_new_unscoped_models` fails CI when any model outside the `KNOWN_UNSCOPED` allowlist (127-entry snapshot) is unscoped. The allowlist must only shrink.
- [ ] **Audit bypass paths.** Catalog uses of `.entire()`, `_base_manager`, `raw()`, and direct SQL; wrap or flag each.
- [ ] **Baseline threat model.** Document every place cross-tenant data could surface (API, exports, reports, mail previews, notifications, admin).

**Exit criteria:** Coverage inventory published; fail-closed manager merged behind a flag; CI scoping test running.

---

## Phase 1 — Complete Data Isolation (Weeks 3–8)

**Objective:** No query path can return another tenant's data.

- [ ] Scope all remaining tenant-owned models (payroll, attendance edge models, biometric, geofencing, facedetection, whatsapp, audit).
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

- [ ] **Tenant model.** Add slug/domain, status (trial/active/suspended), plan FK, created-at to `Company` (or a new `Tenant` 1:1 with `Company`). Support subdomain routing middleware (`<slug>.app.com`) alongside the existing session-based selection.
- [ ] **Provisioning pipeline.** Command/service to create a tenant: company row, default settings, admin user, demo-data option. Must run migrations/seeds without manual steps.
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
- [ ] **Platform admin console.** Cross-tenant operations view for the SaaS operator (uses the fail-closed bypass intentionally): tenant list, health, usage, MRR dashboard, impersonation-with-audit.
- [ ] **Lifecycle ops.** Tenant suspension (read-only), deletion/export (GDPR), backup/restore per tenant (`horilla_backup` extension).

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
2. **Scheduler scale** — single-process apscheduler iterating many tenants will bottleneck; budget a Celery migration if tenant count grows.
3. **Payroll/audit correctness** — scoping payroll wrong is a legal problem, not just a bug; extra review + staging validation.
4. **Global settings refactor** — touches mail delivery for everyone; do it with dual-read fallbacks.

## Estimated effort

~5–6 months for a small team (2–3 engineers) to beta; Phases 0–1 are the non-negotiable foundation, Phases 2–3 can overlap with a second engineer on billing.
