"""Explicitly authorized platform catalog routes."""

import logging
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from saas.models import BillingPlan
from saas.plan_forms import BillingPlanForm
from saas.paystack import PaystackError, publish_plan
from saas.views import platform_required

logger = logging.getLogger(__name__)


@platform_required
@require_GET
def plan_list(request):
    return render(
        request,
        "saas/plans/list.html",
        {"plans": BillingPlan.objects.all()},
        using="platform",
    )


@platform_required
@require_http_methods(["GET", "POST"])
def plan_edit(request, pk=None):
    # Serialize edits against publication so a concurrent draft edit cannot change
    # the local price after the gateway has received an earlier price.
    with transaction.atomic():
        plan = (
            get_object_or_404(BillingPlan.objects.select_for_update(), pk=pk)
            if pk
            else BillingPlan()
        )
        form = BillingPlanForm(
            request.POST if request.method == "POST" else None, instance=plan
        )
        if request.method == "POST" and form.is_valid():
            plan = form.save()
            logger.info(
                "saas.plan.saved plan_id=%s actor_id=%s", plan.pk, request.user.pk
            )
            messages.success(request, "Plan saved.")
            return redirect("plan-edit", pk=plan.pk)
        return render(
            request,
            "saas/plans/edit.html",
            {"form": form, "plan": plan},
            using="platform",
        )


@platform_required
@require_POST
def plan_publish(request, pk):
    get_object_or_404(BillingPlan, pk=pk)
    try:
        publish_plan(pk, actor=request.user)
        messages.success(request, "Plan published to Paystack.")
    except PaystackError as error:
        messages.error(request, str(error))
    return redirect("plan-edit", pk=pk)


@platform_required
@require_POST
def plan_archive(request, pk):
    plan = get_object_or_404(BillingPlan, pk=pk)
    BillingPlan.objects.filter(pk=plan.pk).update(active=False)
    logger.info("saas.plan.archived plan_id=%s actor_id=%s", plan.pk, request.user.pk)
    messages.success(
        request, "Plan archived. Existing subscriptions retain their terms."
    )
    return redirect("plan-list")


@platform_required
@require_POST
def plan_reconcile(request, pk):
    from saas.paystack import reconcile_plan

    get_object_or_404(BillingPlan, pk=pk)
    try:
        reconcile_plan(
            pk, request.POST.get("provider_code", "").strip(), actor=request.user
        )
        messages.success(request, "Existing Paystack plan verified and linked.")
    except PaystackError as error:
        messages.error(request, str(error))
    return redirect("plan-edit", pk=pk)
