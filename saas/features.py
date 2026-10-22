"""Explicit feature catalog and immutable plan terms; foundation HR stays available."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

FEATURE_CHOICES = (
    ("attendance", "Attendance"),
    ("leave", "Leave"),
    ("payroll", "Payroll"),
    ("recruitment", "Recruitment"),
    ("onboarding", "Onboarding"),
    ("offboarding", "Offboarding"),
    ("performance", "Performance"),
    ("projects", "Projects"),
    ("assets", "Assets"),
    ("helpdesk", "Helpdesk"),
    ("devices", "Devices and biometrics"),
    ("reports", "Reports"),
)
APP_FEATURES = dict(
    attendance="attendance",
    leave="leave",
    payroll="payroll",
    recruitment="recruitment",
    onboarding="onboarding",
    offboarding="offboarding",
    pms="performance",
    project="projects",
    asset="assets",
    helpdesk="helpdesk",
    biometric="devices",
    device_gateway="devices",
    facedetection="devices",
    geofencing="attendance",
    report="reports",
)
DEPENDENCIES = {
    "payroll": ("attendance", "leave"),
    "onboarding": ("recruitment",),
    "devices": ("attendance",),
}


def all_features():
    return [key for key, label in FEATURE_CHOICES]


def required_features(feature):
    if feature not in all_features():
        return ()
    return (feature, *DEPENDENCIES.get(feature, ()))


def validate_features(features):
    selected = set(features)
    unknown = selected - set(all_features())
    if unknown:
        raise ValidationError("Choose only supported plan features.")
    labels = dict(FEATURE_CHOICES)
    for key in selected:
        missing = set(DEPENDENCIES.get(key, ())) - selected
        if missing:
            raise ValidationError(
                f"{labels[key]} requires "
                + ", ".join(labels[item] for item in sorted(missing))
                + "."
            )
    return selected


def set_plan_features(plan, features):
    from saas.models import BillingPlan, BillingPlanFeature

    selected = validate_features(features)
    with transaction.atomic():
        current = BillingPlan.objects.select_for_update().get(pk=plan.pk)
        existing = set(current.entitlements.values_list("feature", flat=True))
        if existing == selected:
            return
        if current.publication_state != "draft":
            raise ValidationError(
                "Published or pending plan features are frozen. Create a new plan."
            )
        current.entitlements.all().delete()
        BillingPlanFeature.objects.bulk_create(
            [BillingPlanFeature(plan=current, feature=key) for key in sorted(selected)]
        )


def restrict_feature_companies(companies, feature):
    from saas.models import BillingPlanFeature

    requirements = required_features(feature)
    if not requirements:
        return companies.none()
    for key in requirements:
        plans = BillingPlanFeature.objects.filter(feature=key).values("plan_id")
        companies = companies.filter(
            Q(tenant__subscription__isnull=True)
            | Q(tenant__subscription__plan_id__in=plans)
        )
    return companies


def company_has_feature(company_id, feature):
    from saas.access import active_company_ids

    return bool(
        company_id and active_company_ids([company_id], feature=feature).exists()
    )


def feature_for_model(model):
    label = getattr(model, "_saas_original_model_label", model._meta.label_lower)
    return APP_FEATURES.get(label.split(".")[0])


def scope_has_features(features, company_ids=None, request=None):
    if (
        request is not None
        and request.user.is_authenticated
        and request.user.is_superuser
    ):
        return True
    from saas.scope import effective_company_ids

    ids = set(company_ids if company_ids is not None else effective_company_ids())
    if not ids:
        return False
    from saas.access import active_company_ids

    checks = (
        request.__dict__.setdefault("_saas_feature_checks", {})
        if request is not None
        else {}
    )
    for feature in features:
        key = (tuple(sorted(ids)), feature)
        if key not in checks:
            checks[key] = active_company_ids(ids, feature=feature).count() == len(ids)
        if not checks[key]:
            return False
    return True


def register_feature_defaults():
    from django.db.models.signals import post_save, pre_save, pre_delete
    from saas.models import BillingPlan, BillingPlanFeature

    def seed(sender, instance, created, raw=False, **kwargs):
        if created and not raw:
            # Initial defaults also support trusted published-plan imports. The
            # root draft form subsequently replaces this with its explicit choice.
            BillingPlanFeature.objects.bulk_create(
                [
                    BillingPlanFeature(plan=instance, feature=key)
                    for key in all_features()
                ]
            )

    def guard(sender, instance, raw=False, **kwargs):
        if raw:
            return
        plan_ids = {instance.plan_id}
        if instance.pk:
            original = (
                BillingPlanFeature.objects.filter(pk=instance.pk)
                .values_list("plan_id", flat=True)
                .first()
            )
            if original:
                plan_ids.add(original)
        plans = (
            BillingPlan.objects.select_for_update()
            .filter(pk__in=plan_ids)
            .order_by("pk")
        )
        if any(plan.publication_state != "draft" for plan in plans):
            raise ValidationError("Published plan features are frozen.")

    post_save.connect(
        seed, sender=BillingPlan, weak=False, dispatch_uid="saas.plan.feature_defaults"
    )
    pre_save.connect(
        guard,
        sender=BillingPlanFeature,
        weak=False,
        dispatch_uid="saas.plan.feature_save",
    )
    pre_delete.connect(
        guard,
        sender=BillingPlanFeature,
        weak=False,
        dispatch_uid="saas.plan.feature_delete",
    )


def feature_for_view(view, request=None, view_kwargs=None):
    target = getattr(view, "view_class", getattr(view, "cls", view))
    model = getattr(target, "model", None)
    if model is not None and hasattr(model, "_meta"):
        key = feature_for_model(model)
        if key:
            return key
    parts = getattr(target, "__module__", "").split(".")
    if parts[0] in APP_FEATURES:
        return APP_FEATURES[parts[0]]
    if parts[0] == "horilla_api":
        for part in parts[1:]:
            if part in APP_FEATURES:
                return APP_FEATURES[part]
    kwargs = view_kwargs or {}
    if kwargs.get("app_label") in APP_FEATURES:
        return APP_FEATURES[kwargs["app_label"]]
    if request is not None:
        path = request.path_info.strip("/").split("/")
        while path and path[0] in ("api", "v1", "v2"):
            path.pop(0)
        if path:
            if path[0] == "terminal":
                return "devices"
            if path[0] in APP_FEATURES:
                return APP_FEATURES[path[0]]
        match = getattr(request, "resolver_match", None)
        if match:
            name = match.url_name or ""
            if name.startswith(
                ("dashboard-attendance", "dashboard-department-overtime")
            ):
                return "attendance"
            if name.startswith(
                (
                    "dashboard-leave",
                    "dashboard-todays-leave",
                    "dashboard-department-leave",
                )
            ):
                return "leave"
            if name.startswith(("dashboard-recruitment", "dashboard-hiring")):
                return "recruitment"
            if name.startswith("dashboard-payroll"):
                return "payroll"
        if match and match.url_name in (
            "check-in-check-out-setting",
            "clock-in",
            "clock-out",
        ):
            return "attendance"
    return None


def request_feature_allowed(request, feature):
    if not feature:
        return True
    return scope_has_features((feature,), request=request)


def url_feature_allowed(request, url):
    from urllib.parse import urlsplit
    from django.urls import resolve, Resolver404

    try:
        match = resolve(urlsplit(str(url)).path)
    except (Resolver404, ValueError):
        return True
    from types import SimpleNamespace

    context = SimpleNamespace(path_info=urlsplit(str(url)).path, resolver_match=match)
    key = feature_for_view(match.func, context, match.kwargs)
    return request_feature_allowed(request, key)


def report_features_allowed(definition, company_ids=None, request=None):
    features = {"reports"}
    features.update(
        APP_FEATURES[app] for app in definition.required_apps if app in APP_FEATURES
    )
    if definition.export_model:
        app = definition.export_model.split(".")[0]
        if app in APP_FEATURES:
            features.add(APP_FEATURES[app])
    return scope_has_features(features, company_ids=company_ids, request=request)
