"""Billing services: server-priced checkout and verified, replay-safe entitlement."""

import logging
import os
import re
import uuid
from datetime import timedelta
from urllib.parse import urlsplit
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from saas.models import (
    BillingNotice,
    BillingPayment,
    BillingPlan,
    Tenant,
    TenantSubscription,
)
from saas.paystack import api_request, PaystackError, secret_key
from saas.services import require_platform_operator

logger = logging.getLogger(__name__)


class BillingError(Exception):
    pass


def require_billing_admin(tenant, actor):
    if not (
        actor
        and actor.is_authenticated
        and actor.is_active
        and (actor.is_superuser or tenant.primary_admin_id == actor.pk)
    ):
        raise PermissionError(
            "Only the tenant primary administrator or a platform operator can manage billing."
        )


def notice(subscription, key, kind, message):
    BillingNotice.objects.get_or_create(
        key=key, defaults=dict(subscription=subscription, kind=kind, message=message)
    )


def enroll(tenant_id, plan_id, *, trial_days, actor):
    require_platform_operator(actor)
    if (
        isinstance(trial_days, bool)
        or not isinstance(trial_days, int)
        or not 0 <= trial_days <= 365
    ):
        raise BillingError("Choose a trial of 0 to 365 days.")
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        if tenant.deleted_at:
            raise BillingError("Deleted tenants cannot be enrolled.")
        plan = BillingPlan.objects.get(pk=plan_id)
        if not plan.active or plan.publication_state != "published":
            raise BillingError("Choose an available published plan.")
        try:
            environment = domain_for(secret_key())
        except PaystackError:
            raise BillingError(
                "Configure the payment provider before enrolling a tenant."
            ) from None
        if plan.provider_domain != environment:
            raise BillingError(
                "Choose a plan matching the configured Paystack test/live environment."
            )
        if TenantSubscription.objects.filter(tenant=tenant).exists():
            raise BillingError(
                "This tenant is already enrolled. Plan changes are not available yet."
            )
        subscription = TenantSubscription.objects.create(
            tenant=tenant,
            plan=plan,
            trial_until=timezone.now() + timedelta(days=trial_days),
        )
        notice(
            subscription,
            f"enrolled:{subscription.pk}",
            "enrolled",
            "Your billing plan is ready. Review the price and trial expiry below.",
        )
    logger.info(
        "saas.billing.enrolled tenant_id=%s plan_id=%s actor_id=%s",
        tenant_id,
        plan_id,
        actor.pk,
    )
    return subscription


def domain_for(key):
    return "test" if key.startswith("sk_test_") else "live"


def public_url():
    value = (
        getattr(settings, "SAAS_PUBLIC_URL", "")
        or os.environ.get("SAAS_PUBLIC_URL", "")
    ).rstrip("/")
    parts = urlsplit(value)
    if not (
        parts.scheme == "https"
        and parts.hostname
        and not parts.username
        and not parts.password
        and not parts.query
        and not parts.fragment
        and not parts.path
    ):
        raise BillingError(
            "Payments are not configured. The platform administrator must set a trusted public HTTPS origin."
        )
    return value


def start_checkout(tenant_id, *, actor, auto_renew=False):
    key = secret_key()
    origin = public_url()
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        require_billing_admin(tenant, actor)
        if tenant.deleted_at:
            raise BillingError("Deleted tenants cannot pay.")
        subscription = TenantSubscription.objects.select_for_update().get(tenant=tenant)
        plan = subscription.plan
        if plan.provider_domain != domain_for(key):
            raise BillingError("This plan belongs to a different Paystack environment.")
        if subscription.renewal_state in (
            "pending",
            "active",
            "uncertain",
            "canceling",
        ):
            raise BillingError(
                "Resolve or cancel automatic renewal before starting another checkout."
            )
        if auto_renew and subscription.provider_code:
            raise BillingError(
                "Automatic renewal cannot be restarted after cancellation yet. Use manual renewal."
            )
        pending = subscription.payments.filter(
            status__in=["pending", "ready", "uncertain"]
        ).first()
        if pending:
            if pending.status == "ready" and pending.checkout_url:
                pending.auto_renew = bool(auto_renew)
                pending.save(update_fields=["auto_renew"])
                return pending
            raise BillingError(
                "An earlier checkout needs verification. Check its payment status below."
            )
        admin = tenant.primary_admin
        try:
            email = admin.email.strip().lower() if admin and admin.is_active else ""
            if email and subscription.customer_code:
                email = (
                    subscription.payments.filter(status="paid")
                    .order_by("-paid_at")
                    .values_list("email", flat=True)
                    .first()
                    or email
                )
            validate_email(email)
        except ValidationError:
            raise BillingError(
                "Assign an active tenant administrator with a valid billing email."
            ) from None
        payment = BillingPayment.objects.create(
            subscription=subscription,
            plan=plan,
            reference="saas-" + uuid.uuid4().hex,
            amount=plan.amount,
            currency=plan.currency,
            domain=plan.provider_domain,
            email=email,
            auto_renew=bool(auto_renew),
        )
    logger.info(
        "saas.billing.checkout_created payment_id=%s actor_id=%s auto_renew=%s",
        payment.pk,
        actor.pk,
        payment.auto_renew,
    )
    try:
        data = api_request(
            "POST",
            "transaction/initialize",
            payload=dict(
                email=payment.email,
                amount=payment.amount,
                currency=payment.currency,
                reference=payment.reference,
                callback_url=origin + reverse("paystack-callback"),
            ),
            key=key,
        )
        url = data.get("authorization_url", "")
        parsed = urlsplit(url)
        if (
            data.get("reference") != payment.reference
            or parsed.scheme != "https"
            or parsed.netloc != "checkout.paystack.com"
            or len(url) > 300
        ):
            raise PaystackError("Paystack returned an invalid checkout response.")
        BillingPayment.objects.filter(pk=payment.pk).update(
            status="ready", checkout_url=url
        )
    except Exception:
        BillingPayment.objects.filter(pk=payment.pk).update(status="uncertain")
        raise BillingError(
            "Checkout could not be confirmed. Verify this payment before trying again."
        ) from None
    payment.refresh_from_db()
    return payment


def provider_date(value):
    try:
        result = parse_datetime(value) if isinstance(value, str) else None
    except (ValueError, TypeError):
        result = None
    if not result or timezone.is_naive(result):
        raise BillingError("The provider returned an invalid billing date.")
    return result


def period_delta(plan):
    return (
        relativedelta(months=1)
        if plan.interval == "monthly"
        else relativedelta(years=1)
    )


def verify_payment(reference):
    payment = (
        BillingPayment.objects.select_related("subscription__plan")
        .filter(reference=reference)
        .first()
    )
    if not payment:
        return None
    recurring_until = payment.period_end
    recurring_start = payment.period_start
    key = secret_key()
    if payment.domain != domain_for(key):
        raise BillingError("This payment belongs to a different Paystack environment.")
    data = api_request("GET", "transaction/verify/" + payment.reference, key=key)
    customer = data.get("customer") or {}
    if not isinstance(customer, dict) or not (
        data.get("reference") == payment.reference
        and data.get("amount") == payment.amount
        and data.get("currency") == payment.currency
        and data.get("domain") == payment.domain
        and str(customer.get("email", "")).casefold() == payment.email.casefold()
        and re.fullmatch(
            r"CUS_[A-Za-z0-9]{1,90}", str(customer.get("customer_code", ""))
        )
    ):
        raise BillingError(
            "Payment details do not match the stored checkout. Access was not changed."
        )
    if data.get("status") != "success":
        if data.get("status") in ("failed", "abandoned", "reversed"):
            BillingPayment.objects.filter(pk=payment.pk).exclude(status="paid").update(
                status="failed"
            )
        return payment
    paid_at = provider_date(data.get("paid_at") or data.get("paidAt"))
    if (
        recurring_start is not None
        and abs((paid_at - recurring_start).total_seconds()) > 7 * 86400
    ):
        raise BillingError("Payment time does not match the recurring invoice period.")
    if paid_at > timezone.now() + timedelta(minutes=5):
        raise BillingError("The provider payment time is in the future.")
    with transaction.atomic():
        subscription = TenantSubscription.objects.select_for_update().get(
            pk=payment.subscription_id
        )
        payment = BillingPayment.objects.select_for_update().get(pk=payment.pk)
        if (
            subscription.customer_code
            and subscription.customer_code != customer["customer_code"]
        ):
            raise BillingError("Payment customer does not match this subscription.")
        if payment.status != "paid":
            if payment.plan_id != subscription.plan_id:
                raise BillingError("The payment plan does not match this subscription.")
            subscription.customer_code = customer["customer_code"]
            subscription.customer_id = customer.get("id")
            if recurring_until is not None:
                until = max(subscription.access_until, recurring_until)
            else:
                until = max(timezone.now(), subscription.access_until) + period_delta(
                    subscription.plan
                )
            subscription.paid_until = until
            subscription.save(
                update_fields=["customer_code", "customer_id", "paid_until"]
            )
            payment.status = "paid"
            payment.paid_at = paid_at
            payment.credited_until = until
            payment.save(update_fields=["status", "paid_at", "credited_until"])
            notice(
                subscription,
                "paid:" + payment.reference,
                "payment_success",
                "Payment confirmed. Your paid access has been extended.",
            )
    logger.info(
        "saas.billing.payment_confirmed payment_id=%s tenant_id=%s",
        payment.pk,
        subscription.tenant_id,
    )
    if payment.auto_renew and recurring_until is None:
        setup_renewal(subscription.pk, data.get("authorization") or {})
    return payment


def setup_renewal(subscription_id, authorization):
    """Claim once before an external create; a crash must not create two subscriptions."""
    tenant_id = TenantSubscription.objects.values_list("tenant_id", flat=True).get(
        pk=subscription_id
    )
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        subscription = TenantSubscription.objects.select_for_update().get(
            pk=subscription_id
        )
        if (
            tenant.deleted_at
            or not subscription.paid_until
            or subscription.paid_until <= timezone.now()
            or subscription.provider_code
            or subscription.renewal_state not in ("none", "unavailable")
        ):
            return
        if not (
            isinstance(authorization, dict)
            and authorization.get("reusable") is True
            and re.fullmatch(
                r"AUTH_[A-Za-z0-9]{1,100}",
                str(authorization.get("authorization_code", "")),
            )
        ):
            subscription.renewal_state = "unavailable"
            subscription.save(update_fields=["renewal_state"])
            notice(
                subscription,
                f"renewal-unavailable:{subscription.pk}",
                "renewal_unavailable",
                "This payment method cannot renew automatically. Renew manually before access expires.",
            )
            return
        subscription.renewal_state = "pending"
        subscription.renewal_started_at = timezone.now()
        subscription.save(update_fields=["renewal_state", "renewal_started_at"])
    try:
        data = api_request(
            "POST",
            "subscription",
            payload=dict(
                customer=subscription.customer_code,
                plan=subscription.plan.provider_code,
                authorization=authorization["authorization_code"],
                start_date=subscription.paid_until.isoformat(),
            ),
        )
        code = data.get("subscription_code")
        token = data.get("email_token")
        if not (
            isinstance(code, str)
            and re.fullmatch(r"SUB_[A-Za-z0-9]{1,90}", code)
            and isinstance(token, str)
            and 0 < len(token) <= 200
            and data.get("domain") == subscription.plan.provider_domain
            and data.get("amount") == subscription.plan.amount
        ):
            raise BillingError("Unconfirmed automatic renewal.")
        confirmed = remote_subscription(subscription, code)
        next_payment = provider_date(confirmed.get("next_payment_date"))
        if (
            abs((next_payment - subscription.paid_until).total_seconds()) > 300
            or confirmed["status"] != "active"
        ):
            raise BillingError("The renewal schedule does not match the paid period.")
        TenantSubscription.objects.filter(pk=subscription.pk).update(
            provider_code=code,
            provider_token=confirmed["email_token"],
            renewal_state="active",
        )
    except Exception:
        TenantSubscription.objects.filter(pk=subscription.pk).update(
            renewal_state="uncertain"
        )
        logger.warning(
            "saas.billing.renewal_uncertain subscription_id=%s", subscription.pk
        )
        notice(
            subscription,
            f"renewal-uncertain:{subscription.pk}",
            "renewal_uncertain",
            "Payment succeeded, but automatic renewal needs platform reconciliation. Do not start a second subscription.",
        )


def cancel_renewal(tenant_id, *, actor):
    with transaction.atomic():
        subscription = TenantSubscription.objects.select_for_update().get(
            tenant_id=tenant_id
        )
        require_billing_admin(subscription.tenant, actor)
        if subscription.renewal_state == "off":
            return
        if not subscription.provider_code or not subscription.provider_token:
            raise BillingError(
                "The platform must reconcile renewal setup before cancellation."
            )
        if domain_for(secret_key()) != subscription.plan.provider_domain:
            raise BillingError(
                "This subscription belongs to another Paystack environment."
            )
        subscription.renewal_state = "canceling"
        subscription.save(update_fields=["renewal_state"])
    api_request(
        "POST",
        "subscription/disable",
        payload=dict(
            code=subscription.provider_code, token=subscription.provider_token
        ),
        allow_empty=True,
    )
    TenantSubscription.objects.filter(pk=subscription.pk).update(renewal_state="off")
    notice(
        subscription,
        f"renewal-canceled:{subscription.pk}",
        "renewal_canceled",
        "Automatic renewal is canceled. Paid access continues until the date shown below.",
    )


def remote_subscription(subscription, code):
    if not re.fullmatch(r"SUB_[A-Za-z0-9]{1,90}", code):
        raise BillingError("Enter a valid Paystack subscription code.")
    if domain_for(secret_key()) != subscription.plan.provider_domain:
        raise BillingError("This subscription belongs to another Paystack environment.")
    data = api_request("GET", "subscription/" + code)
    customer = data.get("customer") or {}
    plan = data.get("plan") or {}
    if not (
        isinstance(customer, dict)
        and isinstance(plan, dict)
        and data.get("subscription_code") == code
        and data.get("domain") == subscription.plan.provider_domain
        and data.get("amount") == subscription.plan.amount
        and customer.get("customer_code") == subscription.customer_code
        and plan.get("plan_code") == subscription.plan.provider_code
        and plan.get("amount") == subscription.plan.amount
        and plan.get("currency") == subscription.plan.currency
        and plan.get("interval") == subscription.plan.interval
        and isinstance(data.get("email_token"), str)
        and 0 < len(data["email_token"]) <= 200
    ):
        raise BillingError("Provider subscription does not match this tenant and plan.")
    if data.get("status") not in ("active", "non-renewing", "cancelled", "completed"):
        raise BillingError(
            "Review this subscription status in Paystack before linking it."
        )
    return data


def reconcile_renewal(tenant_id, code, *, actor):
    require_platform_operator(actor)
    subscription = TenantSubscription.objects.select_related("plan").get(
        tenant_id=tenant_id
    )
    if subscription.renewal_state not in ("uncertain", "pending", "canceling"):
        raise BillingError(
            "Only an unconfirmed renewal or cancellation can be reconciled."
        )
    if (
        subscription.renewal_state == "pending"
        and subscription.renewal_started_at
        and subscription.renewal_started_at > timezone.now() - timedelta(minutes=5)
    ):
        raise BillingError(
            "Renewal setup is still in progress. Wait five minutes before reconciliation."
        )
    data = remote_subscription(subscription, code)
    from django.db import IntegrityError

    try:
        with transaction.atomic():
            current = TenantSubscription.objects.select_for_update().get(
                pk=subscription.pk
            )
            if current.renewal_state not in ("uncertain", "pending", "canceling"):
                raise BillingError("Renewal state changed. Reload this page.")
            if current.provider_code and current.provider_code != code:
                raise BillingError(
                    "This tenant is already linked to a different provider subscription."
                )
            current.provider_code = code
            current.provider_token = data["email_token"]
            current.renewal_state = "active" if data["status"] == "active" else "off"
            current.save(
                update_fields=["provider_code", "provider_token", "renewal_state"]
            )
    except IntegrityError:
        raise BillingError(
            "This provider subscription is already linked to another tenant."
        ) from None
    logger.info(
        "saas.billing.renewal_reconciled tenant_id=%s actor_id=%s", tenant_id, actor.pk
    )


def close_reviewed_checkout(tenant_id, reference, *, confirmation, actor):
    require_platform_operator(actor)
    if confirmation != reference:
        raise BillingError("Type the exact payment reference after checking Paystack.")
    with transaction.atomic():
        subscription = TenantSubscription.objects.select_for_update().get(
            tenant_id=tenant_id
        )
        payment = BillingPayment.objects.select_for_update().get(
            subscription=subscription, reference=reference
        )
        if payment.status not in ("pending", "ready", "uncertain"):
            raise BillingError("Only an unresolved checkout can be closed.")
        if payment.created_at > timezone.now() - timedelta(minutes=5):
            raise BillingError(
                "This checkout is recent. Wait five minutes and check Paystack before closing it."
            )
        payment.status = "closed"
        payment.auto_renew = False
        payment.save(update_fields=["status", "auto_renew"])
    logger.info(
        "saas.billing.checkout_closed payment_id=%s actor_id=%s", payment.pk, actor.pk
    )
