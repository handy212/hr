"""Signed event handling. No unverified event directly grants paid access."""

import re
from datetime import timedelta
from django.db import transaction
from saas.billing import (
    BillingError,
    domain_for,
    notice,
    provider_date,
    verify_payment,
)
from saas.models import BillingPayment, TenantSubscription
from saas.paystack import secret_key


def process_event(event, data):
    if event == "charge.success":
        reference = data.get("reference")
        if isinstance(reference, str) and len(reference) <= 100:
            payment = verify_payment(reference)
            if payment is not None and payment.status != "paid":
                raise BillingError(
                    "Successful charge is not yet verified; retry this event."
                )
        return
    if event not in (
        "invoice.update",
        "invoice.payment_failed",
        "subscription.disable",
        "subscription.not_renew",
    ):
        return
    details = data.get("subscription") or data
    if not isinstance(details, dict):
        raise BillingError("Invalid subscription event.")
    subscription = (
        TenantSubscription.objects.select_related("plan")
        .filter(provider_code=details.get("subscription_code"))
        .first()
    )
    if not subscription or not subscription.provider_code:
        return
    if (
        data.get("domain") != subscription.plan.provider_domain
        or domain_for(secret_key()) != subscription.plan.provider_domain
    ):
        raise BillingError("Wrong event environment.")
    if event.startswith("subscription."):
        TenantSubscription.objects.filter(pk=subscription.pk).update(
            renewal_state="off"
        )
        notice(
            subscription,
            f"provider-disabled:{subscription.pk}",
            "renewal_canceled",
            "Paystack reports automatic renewal has stopped. Existing paid access remains available.",
        )
        return
    customer = data.get("customer") or {}
    if (
        not isinstance(customer, dict)
        or customer.get("customer_code") != subscription.customer_code
    ):
        raise BillingError("Invoice customer does not match.")
    invoice = data.get("invoice_code", "")
    if not isinstance(invoice, str) or not re.fullmatch(
        r"INV_[A-Za-z0-9]{1,90}", invoice
    ):
        raise BillingError("Invalid invoice code.")
    if event == "invoice.payment_failed" or data.get("paid") is not True:
        notice(
            subscription,
            "invoice-failed:" + invoice,
            "payment_failed",
            "A subscription payment failed. Check your payment method before paid access expires.",
        )
        return
    charge = data.get("transaction") or {}
    reference = charge.get("reference") if isinstance(charge, dict) else None
    if not isinstance(reference, str) or not re.fullmatch(
        r"[A-Za-z0-9.=-]{1,100}", reference
    ):
        raise BillingError("Invalid renewal reference.")
    start = provider_date(data.get("period_start"))
    end = provider_date(data.get("period_end"))
    minimum, maximum = (
        (27, 32) if subscription.plan.interval == "monthly" else (364, 367)
    )
    if not timedelta(days=minimum) <= end - start <= timedelta(days=maximum):
        raise BillingError("Invalid invoice period.")
    email = customer.get("email")
    if not isinstance(email, str) or len(email) > 254:
        raise BillingError("Invalid customer email.")
    with transaction.atomic():
        TenantSubscription.objects.select_for_update().get(pk=subscription.pk)
        payment, created = BillingPayment.objects.get_or_create(
            reference=reference,
            defaults=dict(
                subscription=subscription,
                plan=subscription.plan,
                amount=subscription.plan.amount,
                currency=subscription.plan.currency,
                domain=subscription.plan.provider_domain,
                email=email,
                period_start=start,
                period_end=end,
            ),
        )
        if (
            payment.subscription_id != subscription.pk
            or payment.plan_id != subscription.plan_id
        ):
            raise BillingError("Reference belongs to a different subscription.")
    payment = verify_payment(reference)
    if payment is not None and payment.status != "paid":
        raise BillingError("Invoice charge is not yet verified; retry this event.")
