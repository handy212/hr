"""Session-based support access; original operator authority is rechecked each request."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.crypto import constant_time_compare
from django.utils.cache import patch_cache_control

from saas.models import Tenant
from employee.models import EmployeeWorkInformation

logger = logging.getLogger(__name__)
SESSION_KEY = "tenant_impersonation"
STOP_PATH = "/settings/tenants/impersonation/stop/"


def eligible_admin(tenant):
    user = tenant.primary_admin
    return bool(
        user
        and user.is_active
        and not user.is_superuser
        and EmployeeWorkInformation.objects.unscoped()
        .filter(
            employee_id__employee_user_id=user.pk,
            employee_id__is_active=True,
            company_id=tenant.company_id,
        )
        .exists()
    )


def original_operator(state):
    user = (
        get_user_model()
        .objects.filter(pk=state.get("operator"), is_active=True, is_superuser=True)
        .first()
    )
    if user and constant_time_compare(
        user.get_session_auth_hash(), state.get("hash", "")
    ):
        return user
    return None


def end_impersonation(request):
    state = request.session.get(SESSION_KEY, {})
    operator = original_operator(state)
    if not operator:
        logout(request)
        return redirect("login")
    login(request, operator, backend=state["backend"])
    request.session.pop(SESSION_KEY, None)
    for key in ("selected_company", "selected_company_instance"):
        request.session.pop(key, None)
    request.session["otp_code_verified"] = state["otp_verified"]
    logger.info(
        "Tenant impersonation stopped actor_id=%s tenant_id=%s",
        operator.pk,
        state["tenant"],
    )
    return redirect("tenant-list")


class TenantImpersonationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        state = request.session.get(SESSION_KEY)
        if not state:
            return self.get_response(request)
        if not original_operator(state):
            logout(request)
            return redirect("login")
        tenant = (
            Tenant.objects.select_related("company", "primary_admin")
            .filter(pk=state["tenant"], deleted_at__isnull=True)
            .first()
        )
        valid = (
            settings.COMPANY_SCOPING_STRICT
            and settings.COMPANY_SCOPED_PERMISSIONS
            and tenant
            and tenant.status != Tenant.Status.SUSPENDED
            and eligible_admin(tenant)
            and request.user.pk == tenant.primary_admin_id
        )
        if request.path_info != STOP_PATH and not valid:
            return end_impersonation(request)
        request.impersonation_tenant = tenant
        if request.path_info.rstrip("/") in (
            "/change-password",
            "/update-selected-company",
        ):
            return HttpResponseForbidden(
                "Return to the platform before changing account or company settings."
            )
        if tenant:
            request.session["selected_company"] = str(tenant.company_id)
        response = self.get_response(request)
        patch_cache_control(response, private=True, no_store=True)
        return response
