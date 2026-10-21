"""Plan pricing, publication, and platform authorization contracts."""

from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from horilla.testkit import make_user, make_company, make_employee
from horilla.testkit.company import CompanyFilterTestMixin
from saas.models import BillingPlan
from saas.plan_forms import BillingPlanForm
from saas.paystack import publish_plan, PaystackError


@override_settings(
    TWO_FACTORS_AUTHENTICATION=False,
    COMPANY_SCOPING_STRICT=True,
    COMPANY_SCOPED_PERMISSIONS=True,
    PAYSTACK_SECRET_KEY="sk_test_example",
)
class PlanTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.clear_company_context()
        self.operator = make_user("plans_operator", is_superuser=True)
        self.payload = dict(
            name="Growth",
            price="199.99",
            currency="NGN",
            interval="monthly",
            active="on",
        )

    def test_crud_requires_platform_and_csrf(self):
        member = make_user("plans_member")
        make_employee(
            company=make_company("Member company"),
            user=member,
            email="plans@example.test",
        )
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("plan-list")).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("plan-create"), self.payload).status_code, 403
        )
        self.client.force_login(self.operator)
        response = self.client.post(reverse("plan-create"), self.payload)
        plan = BillingPlan.objects.get()
        self.assertEqual(plan.amount, 19999)
        self.assertRedirects(response, reverse("plan-edit", args=[plan.pk]))
        self.assertContains(self.client.get(reverse("plan-list")), "Growth")
        from django.test import Client

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.operator)
        self.assertEqual(
            csrf_client.post(reverse("plan-create"), self.payload).status_code, 403
        )

    def test_money_rejects_rounding_negative_and_unsupported_currency(self):
        for changes in [
            {"price": "1.001"},
            {"price": "-1"},
            {"price": "0"},
            {"currency": "ZZZ"},
        ]:
            form = BillingPlanForm(data={**self.payload, **changes})
            self.assertFalse(form.is_valid(), changes)

    @patch("saas.paystack.requests.request")
    def test_publication_is_explicit_and_pricing_is_frozen(self, request):
        plan = BillingPlanForm(data=self.payload).save()
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {
            "status": True,
            "data": dict(
                plan_code="PLN_example",
                domain="test",
                amount=19999,
                currency="NGN",
                interval="monthly",
            ),
        }
        publish_plan(plan.pk, actor=self.operator)
        plan.refresh_from_db()
        self.assertEqual(plan.provider_code, "PLN_example")
        self.assertEqual(request.call_args.kwargs["json"]["amount"], 19999)
        publish_plan(plan.pk, actor=self.operator)
        self.assertEqual(request.call_count, 1)
        form = BillingPlanForm(data={**self.payload, "price": "200"}, instance=plan)
        self.assertFalse(form.is_valid())
        plan.amount = 20000
        with self.assertRaises(ValidationError):
            plan.save()

    @patch("saas.paystack.requests.request")
    def test_ambiguous_publication_cannot_blindly_retry(self, request):
        import requests

        plan = BillingPlanForm(data=self.payload).save()
        request.side_effect = requests.Timeout()
        with self.assertRaises(PaystackError):
            publish_plan(plan.pk, actor=self.operator)
        with self.assertRaises(PaystackError):
            publish_plan(plan.pk, actor=self.operator)
        self.assertEqual(request.call_count, 1)
        plan.refresh_from_db()
        self.assertEqual(plan.publication_state, "uncertain")

    def test_archive_keeps_history_and_post_is_required(self):
        plan = BillingPlanForm(data=self.payload).save()
        self.client.force_login(self.operator)
        self.assertEqual(
            self.client.get(reverse("plan-archive", args=[plan.pk])).status_code, 405
        )
        self.client.post(reverse("plan-archive", args=[plan.pk]))
        plan.refresh_from_db()
        self.assertFalse(plan.active)
        self.assertEqual(BillingPlan.objects.count(), 1)

    @override_settings(TWO_FACTORS_AUTHENTICATION=True)
    def test_otp_gate_applies_to_plans(self):
        self.client.force_login(self.operator)
        self.assertRedirects(
            self.client.get(reverse("plan-list")),
            reverse("two-factor"),
            fetch_redirect_response=False,
        )

    @override_settings(PAYSTACK_SECRET_KEY="")
    @patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": ""})
    @patch("saas.paystack.requests.request")
    def test_missing_configuration_preserves_editable_draft(self, request):
        plan = BillingPlanForm(data=self.payload).save()
        with self.assertRaises(PaystackError):
            publish_plan(plan.pk, actor=self.operator)
        plan.refresh_from_db()
        self.assertEqual(plan.publication_state, "draft")
        request.assert_not_called()

    @patch("saas.paystack.requests.request")
    def test_provider_price_mismatch_is_not_published(self, request):
        plan = BillingPlanForm(data=self.payload).save()
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {
            "status": True,
            "data": dict(
                plan_code="PLN_wrong",
                domain="live",
                amount=1,
                currency="NGN",
                interval="monthly",
            ),
        }
        with self.assertRaises(PaystackError):
            publish_plan(plan.pk, actor=self.operator)
        plan.refresh_from_db()
        self.assertIsNone(plan.provider_code)
        self.assertEqual(plan.publication_state, "uncertain")

    @patch("saas.paystack.requests.request")
    def test_publication_uses_fixed_host_and_bounded_transport(self, request):
        plan = BillingPlanForm(data=self.payload).save()
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {
            "status": True,
            "data": dict(
                plan_code="PLN_transport",
                domain="test",
                amount=19999,
                currency="NGN",
                interval="monthly",
            ),
        }
        publish_plan(plan.pk, actor=self.operator)
        self.assertEqual(
            request.call_args.args, ("POST", "https://api.paystack.co/plan")
        )
        self.assertEqual(request.call_args.kwargs["timeout"], (5, 25))
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    @patch("saas.paystack.requests.request")
    def test_uncertain_publication_can_be_reconciled_without_second_post(self, request):
        from saas.paystack import reconcile_plan

        plan = BillingPlanForm(data=self.payload).save()
        BillingPlan.objects.filter(pk=plan.pk).update(publication_state="uncertain")
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {
            "status": True,
            "data": dict(
                plan_code="PLN_existing",
                name="Growth",
                domain="test",
                amount=19999,
                currency="NGN",
                interval="monthly",
            ),
        }
        reconcile_plan(plan.pk, "PLN_existing", actor=self.operator)
        plan.refresh_from_db()
        self.assertEqual(plan.publication_state, "published")
        self.assertEqual(
            request.call_args.args, ("GET", "https://api.paystack.co/plan/PLN_existing")
        )

    @patch("saas.paystack.requests.request")
    def test_reconciliation_rejects_foreign_price_and_unsafe_code(self, request):
        from saas.paystack import reconcile_plan

        plan = BillingPlanForm(data=self.payload).save()
        BillingPlan.objects.filter(pk=plan.pk).update(publication_state="uncertain")
        with self.assertRaises(PaystackError):
            reconcile_plan(plan.pk, "../transaction", actor=self.operator)
        request.assert_not_called()
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {
            "status": True,
            "data": dict(
                plan_code="PLN_existing",
                name="Growth",
                domain="test",
                amount=1,
                currency="NGN",
                interval="monthly",
            ),
        }
        with self.assertRaises(PaystackError):
            reconcile_plan(plan.pk, "PLN_existing", actor=self.operator)
        plan.refresh_from_db()
        self.assertIsNone(plan.provider_code)

    @patch("saas.paystack.requests.request")
    def test_reconciliation_waits_for_inflight_publication(self, request):
        from saas.paystack import reconcile_plan

        plan = BillingPlanForm(data=self.payload).save()
        BillingPlan.objects.filter(pk=plan.pk).update(publication_state="pending")
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {"status": True, "data": {}}
        with self.assertRaisesMessage(PaystackError, "still in progress"):
            reconcile_plan(plan.pk, "PLN_existing", actor=self.operator)
        plan.refresh_from_db()
        self.assertEqual(plan.publication_state, "pending")
