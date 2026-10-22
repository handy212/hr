# Plan entitlements

SaaS owns an explicit catalog of optional HR modules. Employee records, organization,
authentication, account settings and billing remain the foundation. Defaults preserve
all current modules for existing plans and unenrolled tenants. New plan drafts allow
any dependency-valid subset, including foundation only. Publishing freezes features
along with price; feature access never substitutes for existing HR permissions.

Boundaries: session views, JWT dispatch, company-scoped model reads (including
history and aggregates), public recruitment, device ingress, scheduled reports and
jobs that iterate core employees. Multi-company queries filter to eligible owners;
a module action spanning several selected companies requires access in every company.
Explicit unscoped maintenance access remains privileged and unchanged. Normal internal
HR initialization may retain/create related module records; disabling access does not
delete data or break employee initialization. No generic ORM write veto is added.

Optional modules: attendance, leave, payroll, recruitment, onboarding, offboarding,
performance, projects, assets, helpdesk, devices, reports. Payroll requires attendance
and leave; onboarding requires recruitment; devices require attendance. Notifications
and exports must respect both report/module access and existing role permissions.

Upstream integrations use centralized SaaS helpers rather than duplicating policies.
Any new upstream module must be classified in the catalog and have its public/job
entry points reviewed. Plan switching/proration remains a separate billing feature.
