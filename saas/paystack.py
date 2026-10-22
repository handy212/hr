"""Fixed-host Paystack transport. Never expose gateway responses or secrets."""

import logging
import os
from datetime import timedelta
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from saas.models import BillingPlan
from saas.services import require_platform_operator

logger = logging.getLogger(__name__)


class PaystackError(Exception):
    pass


def secret_key():
    key = getattr(settings, "PAYSTACK_SECRET_KEY", "") or os.environ.get(
        "PAYSTACK_SECRET_KEY", ""
    )
    if not key.startswith(("sk_test_", "sk_live_")):
        raise PaystackError(
            "Configure PAYSTACK_SECRET_KEY on the server before publishing plans."
        )
    return key


def api_request(method, path, *, payload=None, key=None, allow_empty=False):
    key = key or secret_key()
    try:
        response = requests.request(
            method,
            "https://api.paystack.co/" + path,
            headers={"Authorization": "Bearer " + key},
            json=payload,
            timeout=(5, 25),
            allow_redirects=False,
        )
        body = response.json()
        if (
            response.status_code != 200
            or body.get("status") is not True
            or (not allow_empty and not isinstance(body.get("data"), dict))
        ):
            raise PaystackError("Paystack could not confirm the operation.")
        return body.get("data", {})
    except (requests.RequestException, ValueError, AttributeError) as error:
        raise PaystackError("Paystack could not confirm the operation.") from error


def publish_plan(plan_id, *, actor):
    require_platform_operator(actor)
    key = secret_key()
    with transaction.atomic():
        plan = BillingPlan.objects.select_for_update().get(pk=plan_id)
        if plan.publication_state == "published":
            return plan
        if plan.publication_state != "draft":
            raise PaystackError(
                "Publication needs reconciliation in Paystack before any retry."
            )
        if not plan.active:
            raise PaystackError("Activate this draft before publishing it.")
        from saas.features import validate_features
        from django.core.exceptions import ValidationError

        try:
            validate_features(plan.entitlements.values_list("feature", flat=True))
        except ValidationError:
            raise PaystackError(
                "This plan has invalid feature dependencies. Edit the draft before publishing."
            ) from None
        BillingPlan.objects.filter(pk=plan.pk).update(
            publication_state="pending", updated_at=timezone.now()
        )
    try:
        data = api_request(
            "POST",
            "plan",
            payload=dict(
                name=plan.name,
                amount=plan.amount,
                currency=plan.currency,
                interval=plan.interval,
                send_sms=False,
            ),
            key=key,
        )
        code = data.get("plan_code")
        domain = "test" if key.startswith("sk_test_") else "live"
        if not (
            isinstance(code, str)
            and code.startswith("PLN_")
            and len(code) <= 100
            and data.get("amount") == plan.amount
            and data.get("currency") == plan.currency
            and data.get("interval") == plan.interval
            and data.get("domain") == domain
        ):
            raise PaystackError(
                "Paystack returned inconsistent plan terms; reconciliation is required."
            )
        BillingPlan.objects.filter(pk=plan.pk).update(
            provider_code=code, provider_domain=domain, publication_state="published"
        )
    except Exception:
        BillingPlan.objects.filter(pk=plan.pk).update(publication_state="uncertain")
        logger.warning(
            "saas.plan.publication_uncertain plan_id=%s actor_id=%s", plan.pk, actor.pk
        )
        raise PaystackError(
            "Publication could not be confirmed. Check Paystack before creating a replacement."
        ) from None
    logger.info("saas.plan.published plan_id=%s actor_id=%s", plan.pk, actor.pk)
    plan.refresh_from_db()
    return plan


def reconcile_plan(plan_id, code, *, actor):
    """Read an operator-selected existing provider plan; never repeat creation."""
    import re

    require_platform_operator(actor)
    if not re.fullmatch(r"PLN_[A-Za-z0-9]{1,90}", code):
        raise PaystackError("Enter a valid Paystack plan code.")
    key = secret_key()
    data = api_request("GET", "plan/" + code, key=key)
    with transaction.atomic():
        plan = BillingPlan.objects.select_for_update().get(pk=plan_id)
        if (
            plan.publication_state == "pending"
            and plan.updated_at > timezone.now() - timedelta(minutes=5)
        ):
            raise PaystackError(
                "Publication is still in progress. Wait five minutes before reconciliation."
            )
        if plan.publication_state not in ("pending", "uncertain"):
            raise PaystackError("Only an unconfirmed publication can be reconciled.")
        if not (
            data.get("plan_code") == code
            and data.get("name") == plan.name
            and data.get("amount") == plan.amount
            and data.get("currency") == plan.currency
            and data.get("interval") == plan.interval
            and data.get("domain") == ("test" if key.startswith("sk_test_") else "live")
        ):
            raise PaystackError(
                "The provider plan does not match these commercial terms and environment."
            )
        from django.db import IntegrityError

        try:
            with transaction.atomic():
                BillingPlan.objects.filter(pk=plan.pk).update(
                    provider_code=code,
                    provider_domain=data["domain"],
                    publication_state="published",
                )
        except IntegrityError:
            raise PaystackError(
                "That provider plan is already linked to another local plan."
            ) from None
    logger.info("saas.plan.reconciled plan_id=%s actor_id=%s", plan.pk, actor.pk)
