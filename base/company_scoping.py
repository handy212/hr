"""
Audit of HorillaCompanyManager coverage across all installed models.

Used by the `audit_company_scoping` management command and the scoping
regression tests to track which models are company-scoped and which are
still cross-tenant leak candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.apps import apps
from django.db import models

from base.horilla_company_manager import HorillaCompanyManager

# Apps/models that are intentionally global (platform-level, auth internals,
# Django/contrib tables). These are excluded from the "gap" report.
GLOBAL_MODELS = {
    ("auth", "group"),
    ("auth", "permission"),
    ("contenttypes", "contenttype"),
    ("sessions", "session"),
    ("sites", "site"),
    ("admin", "logentry"),
    ("axes", "accessattempt"),
    ("axes", "accesslog"),
    ("axes", "accessfailurelog"),
    ("horilla_auth", "horillauser"),
    ("base", "company"),
    ("saas", "resourceownership"),  # private ownership registry; no customer CRUD
    ("base", "companygroupassignment"),  # bounded auth bootstrap queries
    ("employee", "profileeditfeature"),  # platform default; writes protected by SaaS policy
    ("horilla_dbtemplate", "template"),  # platform template editor; no customer access
    ("horilla_dbtemplate", "templateversion"),  # same editor boundary
    ("saas", "tenant"),  # platform control plane; superuser-only management
    ("django_apscheduler", "djangojob"),
    ("django_apscheduler", "djangojobexecution"),
    # Auth/token internals — per-user credentials, no company dimension.
    ("token_blacklist", "outstandingtoken"),
    ("token_blacklist", "blacklistedtoken"),
    ("horilla_auth", "legacyuser"),
    ("horilla_auth", "authusergroups"),
    ("horilla_auth", "authuseruserpermissions"),
    ("axes", "accessattemptexpiration"),
    # Vendor audit trail — global log; horilla_audit carries its own config.
    ("auditlog", "logentry"),
    # Vendor notifications — recipient is a user FK; no company dimension (Phase 1).
    ("notifications", "notification"),
    # Platform-level operations/config.
    ("horilla_backup", "localbackup"),
    ("horilla_backup", "googledrivebackup"),
    ("horilla_ldap", "ldapsettings"),
    ("horilla_theme", "horillacolortheme"),  # shared theme catalog
    ("horilla_audit", "audittag"),  # global tags for history entries
    ("horilla_audit", "accountblockunblock"),  # global feature toggle
    ("horilla_audit", "auditmodelconfig"),  # global audit configuration
    # Per-user/per-employee UI state — stores prefs, not tenant data.
    ("horilla_views", "togglecolumn"),
    ("horilla_views", "activetab"),
    ("horilla_views", "activegroup"),
    ("horilla_views", "savedfilter"),
    ("horilla_views", "activeview"),
    ("horilla_views", "columnorder"),
    ("accessibility", "defaultaccessibility"),  # platform-wide accessibility defaults
    ("base", "driverviewed"),  # per-user onboarding-tour state
}


@dataclass
class ModelScoping:
    app_label: str
    model_name: str
    label: str
    scoped: bool
    company_filter_path: str | None
    has_company_fk: bool
    is_global: bool


@dataclass
class ScopingReport:
    scoped: list[ModelScoping] = field(default_factory=list)
    unscoped_gaps: list[ModelScoping] = field(default_factory=list)
    global_models: list[ModelScoping] = field(default_factory=list)

    @property
    def total(self):
        return len(self.scoped) + len(self.unscoped_gaps) + len(self.global_models)


def _model_has_company_fk(model) -> bool:
    try:
        field = model._meta.get_field("company_id")
    except Exception:
        return False
    return isinstance(field, (models.ForeignKey, models.ManyToManyField))


def audit_model_scoping() -> ScopingReport:
    """
    Walk every installed model and classify it as scoped (default manager is
    a HorillaCompanyManager with a resolvable filter path), an unscoped gap
    (has a company FK but no scoping manager), or intentionally global.
    """
    report = ScopingReport()

    for model in apps.get_models():
        app_label = model._meta.app_label
        model_name = model._meta.model_name
        label = model._meta.label_lower
        is_global = (app_label, model_name) in GLOBAL_MODELS

        manager = getattr(model, "objects", None)
        scoped = isinstance(manager, HorillaCompanyManager)
        path = manager.get_company_filter_path() if scoped else None
        has_fk = _model_has_company_fk(model)

        entry = ModelScoping(
            app_label=app_label,
            model_name=model_name,
            label=label,
            scoped=scoped and bool(path),
            company_filter_path=path,
            has_company_fk=has_fk,
            is_global=is_global,
        )

        if is_global:
            report.global_models.append(entry)
        elif entry.scoped:
            report.scoped.append(entry)
        else:
            report.unscoped_gaps.append(entry)

    return report
