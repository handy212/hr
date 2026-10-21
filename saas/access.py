"""Lifecycle enforcement for authenticated session and JWT tenant access."""

from django.http import HttpResponseForbidden
from django.db.models import Q
from django.utils.translation import gettext as _

from saas.models import Tenant
from horilla.horilla_middlewares import get_selected_company


def scope_is_suspended(company_ids):
    return (
        Tenant.objects.filter(company_id__in=company_ids)
        .filter(Q(status=Tenant.Status.SUSPENDED) | Q(deleted_at__isnull=False))
        .exists()
    )


class TenantAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Legacy HR mail endpoints expose server credentials. Mail transport
        # administration is platform-only, regardless of HR model permissions.
        protected = {
            "mail-server-conf", "mail-server-list", "mail-server-nav",
            "create-mail-server", "update-mail-server", "mail-server-create-update",
            "mail-server-test-email", "mail-server-delete", "replace-primary-mail",
        }
        match = request.resolver_match
        module = getattr(getattr(view_func, "view_class", view_func), "__module__", "")
        if (match and match.url_name in protected) or module.startswith("horilla_dbtemplate."):
            from django.conf import settings
            from django.shortcuts import redirect
            user = request.user
            if not (user.is_authenticated and user.is_active and user.is_superuser):
                return HttpResponseForbidden("Mail server administration requires platform access.")
            if settings.TWO_FACTORS_AUTHENTICATION and not request.session.get("otp_code_verified"):
                return redirect("two-factor")
        return None

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and not user.is_superuser
            and request.path_info.rstrip("/")
            not in (
                "/logout",
                "/update-selected-company",
                "/settings/tenants/impersonation/stop",
            )
        ):
            selected = get_selected_company()
            if selected == "all":
                company_ids = getattr(request, "all_my_company_ids", None)
                if company_ids is None:
                    # Legacy mode lacks a bounded all-company scope.
                    company_ids = Tenant.objects.filter(
                        status=Tenant.Status.SUSPENDED
                    ).values_list("company_id", flat=True)
            else:
                company_ids = [selected] if selected else []
            if scope_is_suspended(company_ids):
                return HttpResponseForbidden(
                    _("This tenant is suspended. Contact your platform administrator.")
                )
        return self.get_response(request)


def blocked_company_ids():
    return Tenant.objects.filter(
        Q(status=Tenant.Status.SUSPENDED) | Q(deleted_at__isnull=False)
    ).values_list('company_id', flat=True)


def active_company_ids(company_ids=None):
    from base.models import Company
    companies = Company.objects.exclude(pk__in=blocked_company_ids())
    if company_ids is not None:
        companies = companies.filter(pk__in=company_ids)
    return companies.values_list("pk", flat=True)


def filter_active_tenants(queryset, company_path, include_shared=True):
    """Normal worker reads exclude inactive owners; explicit bypasses stay explicit."""
    predicate = Q(**{f"{company_path}__in": active_company_ids()})
    if include_shared:
        predicate |= Q(**{f"{company_path}__isnull": True})
    return queryset.filter(predicate).distinct()
