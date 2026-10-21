"""Platform console. Model permissions never grant platform operator access."""

import logging
from functools import wraps

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from saas.models import Tenant
from saas.forms import TenantProvisionForm, TenantStatusForm, TenantUpdateForm
from saas.services import provision_tenant, require_platform_operator

logger = logging.getLogger(__name__)


def platform_required(view):
    @login_required
    @wraps(view)
    def guarded(request, *args, **kwargs):
        try:
            require_platform_operator(request.user)
        except PermissionError:
            return HttpResponseForbidden(_("Platform operator access is required."))
        if settings.TWO_FACTORS_AUTHENTICATION and not request.session.get(
            "otp_code_verified", False
        ):
            return redirect("two-factor")
        return view(request, *args, **kwargs)

    return guarded


@platform_required
@require_GET
def tenant_list(request):
    tenants = (
        Tenant.objects.filter(deleted_at__isnull=True)
        .select_related("company", "primary_admin")
        .annotate(
            employee_count=Count("company__employeeworkinformation", distinct=True)
        )
        .order_by("-created_at", "-pk")
    )
    query = request.GET.get("q", "").strip()[:150]
    status = request.GET.get("status", "")
    totals = dict(
        Tenant.objects.filter(deleted_at__isnull=True)
        .values_list("status")
        .annotate(total=Count("pk"))
    )
    if query:
        tenants = tenants.filter(
            Q(company__company__icontains=query) | Q(slug__icontains=query)
        )
    if status in Tenant.Status.values:
        tenants = tenants.filter(status=status)
    return render(
        request,
        "saas/tenants/list.html",
        {
            "page_obj": Paginator(tenants, 25).get_page(request.GET.get("page")),
            "query": query,
            "selected_status": status,
            "statuses": Tenant.Status.choices,
            "total": sum(totals.values()),
            "trial_count": totals.get("trial", 0),
            "active_count": totals.get("active", 0),
            "suspended_count": totals.get("suspended", 0),
        },
        using="platform",
    )


@platform_required
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("admin_password")
def tenant_create(request):
    form = TenantProvisionForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            tenant = provision_tenant(data=request.POST, actor=request.user)
        except ValidationError as error:
            # Validation can race with another provisioning request. Model-level
            # keys don't necessarily match form fields, so retain a safe summary.
            form.add_error(None, error.messages)
        except IntegrityError:
            form.add_error(
                None,
                _(
                    "The company, tenant slug or admin account already exists. Check the details and try again."
                ),
            )
        else:
            messages.success(
                request,
                _(
                    "Tenant %(slug)s created. Share the admin login and temporary password securely."
                )
                % {"slug": tenant.slug},
            )
            return redirect("tenant-list")
    return render(request, "saas/tenants/create.html", {"form": form}, using="platform")


@platform_required
@require_POST
@transaction.atomic
def tenant_status(request, pk):
    tenant = get_object_or_404(
        Tenant.objects.select_for_update(), pk=pk, deleted_at__isnull=True
    )
    form = TenantStatusForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(_("Choose a valid tenant status."))
    previous = tenant.status
    tenant.status = form.cleaned_data["status"]
    tenant.save(update_fields=["status", "updated_at"])
    logger.info(
        "Tenant status changed tenant_id=%s actor_id=%s previous=%s status=%s",
        tenant.pk,
        request.user.pk,
        previous,
        tenant.status,
    )
    messages.success(request, _("Tenant status updated."))
    return redirect("tenant-list")


@platform_required
@require_GET
def tenant_detail(request, pk):
    tenant = get_object_or_404(
        Tenant.objects.select_related("company", "primary_admin"),
        pk=pk,
        deleted_at__isnull=True,
    )
    return render(
        request, "saas/tenants/detail.html", {"tenant": tenant}, using="platform"
    )


@platform_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def tenant_edit(request, pk):
    tenant = get_object_or_404(
        Tenant.objects.select_related("company").select_for_update(),
        pk=pk,
        deleted_at__isnull=True,
    )
    form = TenantUpdateForm(
        request.POST if request.method == "POST" else None, instance=tenant
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                company = tenant.company
                company.company = form.cleaned_data["company_name"]
                for name in form.company_fields:
                    setattr(company, name, form.cleaned_data[name])
                company.full_clean()
                company.save()
                form.save()
        except ValidationError as error:
            form.add_error(None, error.messages)
        except IntegrityError:
            form.add_error(None, _("The company or tenant slug already exists."))
        else:
            logger.info(
                "Tenant updated tenant_id=%s actor_id=%s", tenant.pk, request.user.pk
            )
            messages.success(request, _("Tenant updated."))
            return redirect("tenant-detail", pk=tenant.pk)
    return render(
        request,
        "saas/tenants/edit.html",
        {"tenant": tenant, "form": form},
        using="platform",
    )


@platform_required
@require_http_methods(["GET", "POST"])
def tenant_delete(request, pk):
    tenant = get_object_or_404(
        Tenant.objects.select_related("company"), pk=pk, deleted_at__isnull=True
    )
    if request.method == "POST":
        if request.POST.get("confirm_slug") != tenant.slug:
            return render(
                request,
                "saas/tenants/delete.html",
                {"tenant": tenant, "confirmation_error": True},
                status=400,
                using="platform",
            )
        with transaction.atomic():
            tenant = get_object_or_404(
                Tenant.objects.select_for_update(), pk=pk, deleted_at__isnull=True
            )
            if request.POST.get("confirm_slug") != tenant.slug:
                return render(
                    request,
                    "saas/tenants/delete.html",
                    {"tenant": tenant, "confirmation_error": True},
                    status=400,
                    using="platform",
                )
            tenant.status = Tenant.Status.SUSPENDED
            tenant.deleted_at = timezone.now()
            tenant.save(update_fields=["status", "deleted_at", "updated_at"])
        logger.info(
            "Tenant deleted with data retained tenant_id=%s actor_id=%s",
            tenant.pk,
            request.user.pk,
        )
        messages.success(
            request, _("Tenant deleted. Access is blocked; HR records are retained.")
        )
        return redirect("tenant-list")
    return render(
        request, "saas/tenants/delete.html", {"tenant": tenant}, using="platform"
    )


@platform_required
@require_POST
def tenant_impersonate(request, pk):
    from django.contrib.auth import BACKEND_SESSION_KEY, login
    from saas.impersonation import SESSION_KEY, eligible_admin

    if not (settings.COMPANY_SCOPING_STRICT and settings.COMPANY_SCOPED_PERMISSIONS):
        return HttpResponseForbidden(
            _(
                "Enable strict company scoping and company-scoped permissions before impersonating tenants."
            )
        )
    tenant = get_object_or_404(
        Tenant.objects.select_related("primary_admin"), pk=pk, deleted_at__isnull=True
    )
    if tenant.status == Tenant.Status.SUSPENDED or not eligible_admin(tenant):
        messages.error(
            request,
            _(
                "Assign an active administrator from this company in Edit before impersonating. Suspended tenants cannot be impersonated."
            ),
        )
        return redirect("tenant-detail", pk=pk)
    state = {
        "operator": request.user.pk,
        "hash": request.user.get_session_auth_hash(),
        "backend": request.session[BACKEND_SESSION_KEY],
        "otp_verified": request.session.get("otp_code_verified", False),
        "tenant": tenant.pk,
    }
    login(request, tenant.primary_admin, backend=state["backend"])
    request.session[SESSION_KEY] = state
    request.session["selected_company"] = str(tenant.company_id)
    request.session["otp_code_verified"] = state["otp_verified"]
    logger.info(
        "Tenant impersonation started actor_id=%s tenant_id=%s target_user_id=%s",
        state["operator"],
        tenant.pk,
        tenant.primary_admin_id,
    )
    return redirect("/")


@login_required
@require_POST
def tenant_impersonation_stop(request):
    from saas.impersonation import SESSION_KEY, end_impersonation

    if not request.session.get(SESSION_KEY):
        return HttpResponseForbidden(_("No impersonation session is active."))
    return end_impersonation(request)
