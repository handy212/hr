"""Tenant billing pages and authenticated Paystack event boundary."""

import hashlib
import hmac
import json
import re
from functools import wraps
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from saas.billing import (
    BillingError,
    cancel_renewal,
    enroll,
    require_billing_admin,
    start_checkout,
    verify_payment,
)
from saas.models import BillingPlan, Tenant, TenantSubscription
from saas.paystack import PaystackError, secret_key
from saas.views import platform_required


def billing_error_message(request, error):
    if isinstance(error, PaystackError) and not request.user.is_superuser:
        return "Payment provider is unavailable. Contact your platform administrator or try again later."
    return str(error)


class EnrollmentForm(forms.Form):
    plan = forms.ModelChoiceField(
        queryset=BillingPlan.objects.filter(active=True, publication_state="published")
    )
    trial_days = forms.IntegerField(min_value=0, max_value=365, initial=14)


class CheckoutForm(forms.Form):
    auto_renew = forms.BooleanField(
        required=False,
        label="Renew automatically at this price each billing period",
        help_text="You can cancel renewal at any time. Your payment method must support reusable authorization.",
    )


def billing_admin_required(view):
    @login_required
    @wraps(view)
    def guarded(request, pk, *args, **kwargs):
        tenant = get_object_or_404(
            Tenant.objects.select_related("company", "primary_admin"),
            pk=pk,
            deleted_at__isnull=True,
        )
        if request.session.get("tenant_impersonation"):
            return HttpResponseForbidden(
                "Return to the platform before managing billing."
            )
        try:
            require_billing_admin(tenant, request.user)
        except PermissionError as error:
            return HttpResponseForbidden(str(error))
        if (
            request.user.is_superuser
            and settings.TWO_FACTORS_AUTHENTICATION
            and not request.session.get("otp_code_verified")
        ):
            return redirect("two-factor")
        return view(request, tenant, *args, **kwargs)

    return guarded


@billing_admin_required
@require_GET
def tenant_billing(request, tenant):
    subscription = (
        TenantSubscription.objects.select_related("plan").filter(tenant=tenant).first()
    )
    checkout_form = CheckoutForm()
    if subscription and subscription.provider_code:
        checkout_form.fields["auto_renew"].disabled = True
        checkout_form.fields[
            "auto_renew"
        ].help_text = "Canceled automatic renewal cannot be restarted yet. You can renew manually."
    return render(
        request,
        "saas/billing/detail.html",
        dict(
            tenant=tenant,
            subscription=subscription,
            form=checkout_form,
            payments=subscription.payments.all()[:30] if subscription else [],
            notices=subscription.notices.all()[:30] if subscription else [],
        ),
        using="platform",
    )


@platform_required
@require_http_methods(["GET", "POST"])
def tenant_enroll(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk, deleted_at__isnull=True)
    form = EnrollmentForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            enroll(
                tenant.pk,
                form.cleaned_data["plan"].pk,
                trial_days=form.cleaned_data["trial_days"],
                actor=request.user,
            )
            messages.success(request, "Tenant enrolled. The trial begins now.")
            return redirect("tenant-billing", pk=tenant.pk)
        except BillingError as error:
            form.add_error(None, str(error))
    return render(
        request,
        "saas/billing/enroll.html",
        dict(tenant=tenant, form=form),
        using="platform",
    )


@billing_admin_required
@require_POST
def tenant_checkout(request, tenant):
    if not TenantSubscription.objects.filter(tenant=tenant).exists():
        return HttpResponseBadRequest("This tenant has not been enrolled.")
    form = CheckoutForm(request.POST)
    if form.is_valid():
        try:
            payment = start_checkout(
                tenant.pk,
                actor=request.user,
                auto_renew=form.cleaned_data["auto_renew"],
            )
            return redirect(payment.checkout_url)
        except (BillingError, PaystackError) as error:
            messages.error(request, billing_error_message(request, error))
    return redirect("tenant-billing", pk=tenant.pk)


@billing_admin_required
@require_POST
def tenant_verify(request, tenant, reference):
    subscription = get_object_or_404(TenantSubscription, tenant=tenant)
    get_object_or_404(subscription.payments, reference=reference)
    try:
        payment = verify_payment(reference)
        messages.success(
            request,
            "Payment confirmed."
            if payment.status == "paid"
            else "Payment has not succeeded yet. No access was added.",
        )
    except (BillingError, PaystackError) as error:
        messages.error(request, billing_error_message(request, error))
    return redirect("tenant-billing", pk=tenant.pk)


@billing_admin_required
@require_POST
def tenant_cancel_renewal(request, tenant):
    get_object_or_404(TenantSubscription, tenant=tenant)
    try:
        cancel_renewal(tenant.pk, actor=request.user)
        messages.success(
            request,
            "Automatic renewal canceled. Your paid access continues until its expiry date.",
        )
    except (BillingError, PaystackError) as error:
        messages.error(request, billing_error_message(request, error))
    return redirect("tenant-billing", pk=tenant.pk)


@require_GET
def paystack_callback(request):
    # Callback parameters are not payment proof. Ask for login and require the
    # tenant's billing authority before displaying or verifying the reference.
    from saas.models import BillingPayment

    reference = request.GET.get("reference", "")[:100]
    payment = (
        BillingPayment.objects.select_related("subscription")
        .filter(reference=reference)
        .first()
    )
    if not payment:
        return HttpResponseBadRequest("Unknown payment reference.")
    return redirect("tenant-billing", pk=payment.subscription.tenant_id)


@csrf_exempt
@require_POST
def paystack_webhook(request):
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > 262144:
            return HttpResponse(status=413)
        payload = request.body
        if len(payload) > 262144:
            return HttpResponse(status=413)
        key = secret_key()
    except (PaystackError, ValueError):
        return HttpResponse(status=503)
    signature = request.headers.get("x-paystack-signature", "")
    expected = hmac.new(key.encode(), payload, hashlib.sha512).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{128}", signature) or not hmac.compare_digest(
        expected, signature
    ):
        return HttpResponseForbidden("Invalid webhook signature.")
    try:
        event = json.loads(payload)
        if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
            return HttpResponseBadRequest("Invalid event.")
        from saas.billing_events import process_event

        process_event(event["event"], event["data"])
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
        return HttpResponseBadRequest("Invalid event.")
    except (BillingError, PaystackError):
        # A failed verify must be retried by Paystack; never acknowledge a
        # successful activation unless its provider verification completed.
        return HttpResponse(status=503)
    return HttpResponse(status=200)


@login_required
@require_GET
def billing_home(request):
    if request.user.is_superuser:
        return redirect("tenant-list")
    tenants = Tenant.objects.filter(primary_admin=request.user, deleted_at__isnull=True)
    if tenants.count() == 1:
        return redirect("tenant-billing", pk=tenants.first().pk)
    return render(
        request, "saas/billing/home.html", {"tenants": tenants}, using="platform"
    )


@platform_required
@require_POST
def tenant_reconcile_renewal(request, pk):
    from saas.billing import reconcile_renewal

    get_object_or_404(TenantSubscription, tenant_id=pk)
    try:
        reconcile_renewal(
            pk, request.POST.get("provider_code", "").strip(), actor=request.user
        )
        messages.success(request, "Provider subscription verified and linked.")
    except (BillingError, PaystackError) as error:
        messages.error(request, billing_error_message(request, error))
    return redirect("tenant-billing", pk=pk)


@billing_admin_required
@require_POST
def tenant_notices_read(request, tenant):
    from django.utils import timezone

    subscription = get_object_or_404(TenantSubscription, tenant=tenant)
    subscription.notices.filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect("tenant-billing", pk=tenant.pk)


@platform_required
@require_POST
def tenant_close_checkout(request, pk, reference):
    from saas.billing import close_reviewed_checkout
    from saas.models import BillingPayment

    get_object_or_404(BillingPayment, subscription__tenant_id=pk, reference=reference)
    try:
        close_reviewed_checkout(
            pk,
            reference,
            confirmation=request.POST.get("confirm_reference", ""),
            actor=request.user,
        )
        messages.success(
            request,
            "Reviewed checkout closed locally. Late payments will still be credited.",
        )
    except BillingError as error:
        messages.error(request, billing_error_message(request, error))
    return redirect("tenant-billing", pk=pk)
