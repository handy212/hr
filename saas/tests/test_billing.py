"""Billing trust boundaries, payment idempotence and lifecycle behavior."""

import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch
from django.test import (
    TestCase,
    TransactionTestCase,
    override_settings,
    skipUnlessDBFeature,
)
from django.urls import reverse
from django.utils import timezone
from horilla.testkit import make_company, make_employee, make_user
from horilla.testkit.company import CompanyFilterTestMixin
from saas.models import BillingPlan, BillingPayment, Tenant, TenantSubscription
from saas.billing import enroll, start_checkout, verify_payment, BillingError


@override_settings(
    COMPANY_SCOPING_STRICT=True,
    COMPANY_SCOPED_PERMISSIONS=True,
    TWO_FACTORS_AUTHENTICATION=False,
    PAYSTACK_SECRET_KEY="sk_test_example",
    SAAS_PUBLIC_URL="https://hr.example.test",
)
class BillingTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.clear_company_context()
        self.operator = make_user("billing_operator", is_superuser=True)
        self.admin = make_user("billing_admin", email="billing@example.test")
        self.company = make_company("Billing company")
        make_employee(
            company=self.company, user=self.admin, email="billing@example.test"
        )
        self.tenant = Tenant.objects.create(
            company=self.company,
            slug="billing",
            status="active",
            primary_admin=self.admin,
        )
        self.plan = BillingPlan.objects.create(
            name="Growth",
            amount=19999,
            currency="NGN",
            interval="monthly",
            publication_state="published",
            provider_code="PLN_growth",
            provider_domain="test",
        )
        self.subscription = enroll(
            self.tenant.pk, self.plan.pk, trial_days=14, actor=self.operator
        )

    def paid_data(self, payment):
        return dict(
            reference=payment.reference,
            status="success",
            amount=19999,
            currency="NGN",
            domain="test",
            customer=dict(
                email="billing@example.test", customer_code="CUS_billing", id=123
            ),
            paid_at=timezone.now().isoformat(),
            authorization=dict(reusable=False),
        )

    @patch("saas.billing.api_request")
    def test_checkout_uses_server_price_and_reuses_pending_attempt(self, api):
        api.side_effect = lambda method, path, **kw: dict(
            reference=kw["payload"]["reference"],
            authorization_url="https://checkout.paystack.com/example",
        )
        payment = start_checkout(self.tenant.pk, actor=self.admin, auto_renew=False)
        self.assertEqual(api.call_args.kwargs["payload"]["amount"], 19999)
        self.assertEqual(
            api.call_args.kwargs["payload"]["email"], "billing@example.test"
        )
        self.assertEqual(
            start_checkout(self.tenant.pk, actor=self.admin, auto_renew=False).pk,
            payment.pk,
        )
        self.assertEqual(api.call_count, 1)

    @patch("saas.billing.api_request")
    def test_verification_is_idempotent_and_preserves_manual_suspension(self, api):
        payment = BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="test-ref",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
        )
        api.return_value = self.paid_data(payment)
        Tenant.objects.filter(pk=self.tenant.pk).update(status="suspended")
        verify_payment(payment.reference)
        self.subscription.refresh_from_db()
        expiry = self.subscription.paid_until
        verify_payment(payment.reference)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.paid_until, expiry)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, "suspended")
        self.assertEqual(
            self.subscription.notices.filter(kind="payment_success").count(), 1
        )

    @patch("saas.billing.api_request")
    def test_mismatched_verification_cannot_grant_access(self, api):
        for index, change in enumerate(
            [
                dict(amount=1),
                dict(currency="USD"),
                dict(domain="live"),
                dict(reference="foreign"),
                dict(
                    customer=dict(
                        email="foreign@example.test", customer_code="CUS_other"
                    )
                ),
            ]
        ):
            payment = BillingPayment.objects.create(
                subscription=self.subscription,
                plan=self.plan,
                reference=f"mismatch-{index}",
                amount=19999,
                currency="NGN",
                domain="test",
                email="billing@example.test",
            )
            api.return_value = {**self.paid_data(payment), **change}
            with self.assertRaises(BillingError):
                verify_payment(payment.reference)
            payment.refresh_from_db()
            self.assertNotEqual(payment.status, "paid")
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.paid_until)

    def test_primary_admin_is_only_tenant_billing_authority(self):
        foreign = make_user("billing_foreign")
        make_employee(company=self.company, user=foreign, email="foreign@example.test")
        self.client.force_login(foreign)
        self.assertEqual(
            self.client.get(
                reverse("tenant-billing", args=[self.tenant.pk])
            ).status_code,
            403,
        )
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                reverse("tenant-billing", args=[self.tenant.pk])
            ).status_code,
            200,
        )

    def test_expiry_blocks_data_but_not_billing_and_legacy_tenants_keep_access(self):
        from saas.access import scope_is_suspended, blocked_company_ids

        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            trial_until=timezone.now() - timedelta(seconds=1)
        )
        self.assertTrue(scope_is_suspended([self.company.pk]))
        self.assertIn(self.company.pk, list(blocked_company_ids()))
        legacy = Tenant.objects.create(
            company=make_company("Legacy"), slug="legacy", status="active"
        )
        self.assertFalse(scope_is_suspended([legacy.company_id]))
        self.client.force_login(self.admin)
        self.assertContains(
            self.client.get(reverse("tenant-billing", args=[self.tenant.pk])), "Growth"
        )

    @patch("saas.billing.api_request")
    def test_webhook_requires_raw_signature_before_processing(self, api):
        payload = json.dumps(
            {"event": "charge.success", "data": {"reference": "unknown"}}
        ).encode()
        url = reverse("paystack-webhook")
        self.assertEqual(
            self.client.post(url, payload, content_type="application/json").status_code,
            403,
        )
        signature = hmac.new(b"sk_test_example", payload, hashlib.sha512).hexdigest()
        self.assertEqual(
            self.client.post(
                url,
                payload,
                content_type="application/json",
                HTTP_X_PAYSTACK_SIGNATURE=signature,
            ).status_code,
            200,
        )
        api.assert_not_called()

    @patch("saas.billing.api_request")
    def test_auto_renewal_is_opt_in_and_starts_after_paid_time(self, api):
        payment = BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="auto-ref",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
            auto_renew=True,
        )
        data = self.paid_data(payment)
        data["authorization"] = {"reusable": True, "authorization_code": "AUTH_example"}
        api.side_effect = lambda method, path, **kw: (
            data
            if path.startswith("transaction/")
            else dict(
                subscription_code="SUB_example",
                email_token="secret-token",
                domain="test",
                amount=19999,
            )
            if method == "POST"
            else dict(
                subscription_code="SUB_example",
                email_token="secret-token",
                domain="test",
                amount=19999,
                status="active",
                customer={"customer_code": "CUS_billing"},
                plan={
                    "plan_code": "PLN_growth",
                    "amount": 19999,
                    "currency": "NGN",
                    "interval": "monthly",
                },
                next_payment_date=TenantSubscription.objects.get(
                    pk=self.subscription.pk
                ).paid_until.isoformat(),
            )
        )
        verify_payment(payment.reference)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.renewal_state, "active")
        creation = [
            call for call in api.call_args_list if call.args == ("POST", "subscription")
        ][0]
        self.assertEqual(
            creation.kwargs["payload"]["start_date"],
            self.subscription.paid_until.isoformat(),
        )
        self.assertEqual(creation.kwargs["payload"]["authorization"], "AUTH_example")

    @patch("saas.billing.api_request")
    def test_uncertain_auto_setup_is_not_created_twice_and_paid_access_remains(
        self, api
    ):
        from saas.paystack import PaystackError

        payment = BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="auto-uncertain",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
            auto_renew=True,
        )
        data = self.paid_data(payment)
        data["authorization"] = {"reusable": True, "authorization_code": "AUTH_example"}
        api.side_effect = [data, PaystackError("timeout"), data]
        verify_payment(payment.reference)
        verify_payment(payment.reference)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.renewal_state, "uncertain")
        self.assertTrue(self.subscription.has_access)
        self.assertEqual(len([c for c in api.call_args_list if c.args[0] == "POST"]), 1)

    @patch("saas.billing.api_request")
    def test_cancel_preserves_paid_time(self, api):
        from saas.billing import cancel_renewal

        until = timezone.now() + timedelta(days=30)
        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            paid_until=until,
            provider_code="SUB_cancel",
            provider_token="private-token",
            renewal_state="active",
        )
        api.return_value = {}
        cancel_renewal(self.tenant.pk, actor=self.admin)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.paid_until, until)
        self.assertEqual(self.subscription.renewal_state, "off")

    @patch("saas.billing.api_request")
    def test_recurring_invoice_replay_uses_period_end(self, api):
        from saas.billing_events import process_event
        from dateutil.relativedelta import relativedelta

        start = timezone.now()
        end = start + relativedelta(months=1)
        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            customer_code="CUS_billing",
            provider_code="SUB_recurring",
            renewal_state="active",
        )
        invoice = dict(
            domain="test",
            subscription={"subscription_code": "SUB_recurring"},
            customer=dict(customer_code="CUS_billing", email="billing@example.test"),
            invoice_code="INV_example",
            paid=True,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            transaction={"reference": "recurring-ref"},
        )
        api.return_value = dict(
            reference="recurring-ref",
            status="success",
            amount=19999,
            currency="NGN",
            domain="test",
            customer=dict(
                email="billing@example.test", customer_code="CUS_billing", id=123
            ),
            paid_at=start.isoformat(),
        )
        process_event("invoice.update", invoice)
        process_event("invoice.update", invoice)
        process_event("charge.success", {"reference": "recurring-ref"})
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.paid_until, end)
        self.assertEqual(
            self.subscription.payments.filter(reference="recurring-ref").count(), 1
        )

    def test_billing_mutations_require_csrf_and_do_not_allow_impersonation(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(
            client.post(reverse("tenant-checkout", args=[self.tenant.pk])).status_code,
            403,
        )
        self.client.force_login(self.admin)
        session = self.client.session
        session["tenant_impersonation"] = {"operator": self.operator.pk}
        session.save()
        # The impersonation middleware rejects this malformed support session, or
        # the billing guard rejects a valid one; neither can authorize a payment.
        with patch("saas.billing_views.start_checkout") as checkout:
            self.client.post(reverse("tenant-checkout", args=[self.tenant.pk]))
            checkout.assert_not_called()

    @patch("saas.billing.api_request")
    def test_revoking_consent_updates_existing_unpaid_checkout(self, api):
        api.side_effect = lambda method, path, **kw: dict(
            reference=kw["payload"]["reference"],
            authorization_url="https://checkout.paystack.com/consent",
        )
        original = start_checkout(self.tenant.pk, actor=self.admin, auto_renew=True)
        revised = start_checkout(self.tenant.pk, actor=self.admin, auto_renew=False)
        self.assertEqual(revised.pk, original.pk)
        revised.refresh_from_db()
        self.assertFalse(revised.auto_renew)

    @patch("saas.billing.api_request")
    def test_success_webhook_retries_when_provider_verification_is_pending(self, api):
        payment = BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="pending-webhook",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
        )
        api.return_value = {**self.paid_data(payment), "status": "pending"}
        payload = json.dumps(
            {"event": "charge.success", "data": {"reference": payment.reference}}
        ).encode()
        signature = hmac.new(b"sk_test_example", payload, hashlib.sha512).hexdigest()
        response = self.client.post(
            reverse("paystack-webhook"),
            payload,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 503)
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.paid_until)

    @patch("saas.billing.api_request")
    def test_reconciliation_matches_customer_plan_and_domain(self, api):
        from saas.billing import reconcile_renewal

        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            renewal_state="uncertain", customer_code="CUS_billing"
        )
        data = dict(
            subscription_code="SUB_recovery",
            email_token="private-token",
            domain="test",
            amount=19999,
            status="active",
            customer={"customer_code": "CUS_billing"},
            plan={
                "plan_code": "PLN_growth",
                "amount": 19999,
                "currency": "NGN",
                "interval": "monthly",
            },
        )
        api.return_value = {**data, "customer": {"customer_code": "CUS_foreign"}}
        with self.assertRaises(BillingError):
            reconcile_renewal(self.tenant.pk, "SUB_recovery", actor=self.operator)
        api.return_value = data
        reconcile_renewal(self.tenant.pk, "SUB_recovery", actor=self.operator)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.provider_code, "SUB_recovery")
        self.assertEqual(self.subscription.renewal_state, "active")

    def test_expiry_notices_are_deduplicated_and_tenant_scoped(self):
        from saas.billing_jobs import notify_expiring_subscriptions

        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            trial_until=timezone.now() - timedelta(seconds=1)
        )
        notify_expiring_subscriptions()
        notify_expiring_subscriptions()
        self.assertEqual(
            self.subscription.notices.filter(kind="access_expired").count(), 1
        )
        foreign = make_user("notice_foreign")
        make_employee(
            company=self.company, user=foreign, email="notice-foreign@example.test"
        )
        self.client.force_login(foreign)
        self.assertEqual(
            self.client.post(
                reverse("tenant-notices-read", args=[self.tenant.pk])
            ).status_code,
            403,
        )
        self.assertTrue(self.subscription.notices.filter(read_at__isnull=True).exists())

    @patch("saas.billing.api_request")
    def test_old_payment_replay_cannot_restart_canceled_renewal(self, api):
        until = timezone.now() + timedelta(days=30)
        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            paid_until=until,
            customer_code="CUS_billing",
            provider_code="SUB_canceled",
            renewal_state="off",
        )
        payment = BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="old-auto",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
            auto_renew=True,
            status="paid",
            credited_until=until,
        )
        data = self.paid_data(payment)
        data["authorization"] = {"reusable": True, "authorization_code": "AUTH_example"}
        api.return_value = data
        verify_payment(payment.reference)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.renewal_state, "off")
        self.assertEqual(self.subscription.paid_until, until)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args[0], "GET")

    def test_tenant_deletion_requires_resolved_renewal_and_checkout(self):
        self.client.force_login(self.operator)
        for state in ["active", "pending", "uncertain", "canceling"]:
            TenantSubscription.objects.filter(pk=self.subscription.pk).update(
                renewal_state=state
            )
            response = self.client.post(
                reverse("tenant-delete", args=[self.tenant.pk]),
                {"confirm_slug": self.tenant.slug},
            )
            self.assertRedirects(
                response, reverse("tenant-billing", args=[self.tenant.pk])
            )
            self.tenant.refresh_from_db()
            self.assertIsNone(self.tenant.deleted_at)
        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            renewal_state="none"
        )
        BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="open-checkout",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
            status="ready",
        )
        self.client.post(
            reverse("tenant-delete", args=[self.tenant.pk]),
            {"confirm_slug": self.tenant.slug},
        )
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.deleted_at)

    def test_callback_does_not_grant_access(self):
        payment = BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="callback-only",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
        )
        response = self.client.get(
            reverse("paystack-callback"), {"reference": payment.reference}
        )
        self.assertRedirects(
            response,
            reverse("tenant-billing", args=[self.tenant.pk]),
            fetch_redirect_response=False,
        )
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.paid_until)

    @patch("saas.billing.api_request")
    def test_changing_primary_admin_email_does_not_change_bound_payment_customer(
        self, api
    ):
        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            customer_code="CUS_billing"
        )
        BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="bound-customer",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
            status="paid",
            paid_at=timezone.now(),
        )
        self.admin.email = "replacement@example.test"
        self.admin.save()
        api.side_effect = lambda method, path, **kw: dict(
            reference=kw["payload"]["reference"],
            authorization_url="https://checkout.paystack.com/customer",
        )
        payment = start_checkout(self.tenant.pk, actor=self.admin)
        self.assertEqual(payment.email, "billing@example.test")

    @patch("saas.billing.api_request")
    def test_reviewed_checkout_revokes_renewal_consent_but_late_payment_is_credited(
        self, api
    ):
        from saas.billing import close_reviewed_checkout

        payment = BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="reviewed-ref",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
            auto_renew=True,
        )
        BillingPayment.objects.filter(pk=payment.pk).update(
            created_at=timezone.now() - timedelta(minutes=10)
        )
        with self.assertRaises(PermissionError):
            close_reviewed_checkout(
                self.tenant.pk,
                payment.reference,
                confirmation=payment.reference,
                actor=self.admin,
            )
        close_reviewed_checkout(
            self.tenant.pk,
            payment.reference,
            confirmation=payment.reference,
            actor=self.operator,
        )
        payment.refresh_from_db()
        self.assertEqual(payment.status, "closed")
        self.assertFalse(payment.auto_renew)
        api.return_value = self.paid_data(payment)
        verify_payment(payment.reference)
        self.subscription.refresh_from_db()
        self.assertIsNotNone(self.subscription.paid_until)
        self.assertEqual(api.call_count, 1)

    @patch("saas.billing.api_request")
    def test_month_end_recurring_invoice_preserves_original_billing_anchor(self, api):
        from datetime import datetime, timezone as datetime_timezone
        from saas.billing_events import process_event

        start = datetime(2026, 2, 28, tzinfo=datetime_timezone.utc)
        end = datetime(2026, 3, 31, tzinfo=datetime_timezone.utc)
        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            trial_until=start - timedelta(days=1),
            customer_code="CUS_billing",
            provider_code="SUB_monthend",
        )
        api.return_value = dict(
            reference="monthend-ref",
            status="success",
            amount=19999,
            currency="NGN",
            domain="test",
            paid_at=start.isoformat(),
            customer=dict(
                email="billing@example.test", customer_code="CUS_billing", id=123
            ),
        )
        process_event(
            "invoice.update",
            dict(
                domain="test",
                subscription={"subscription_code": "SUB_monthend"},
                customer={
                    "customer_code": "CUS_billing",
                    "email": "billing@example.test",
                },
                invoice_code="INV_monthend",
                paid=True,
                period_start=start.isoformat(),
                period_end=end.isoformat(),
                transaction={"reference": "monthend-ref"},
            ),
        )
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.paid_until, end)

    @override_settings(PAYSTACK_SECRET_KEY="")
    @patch.dict("os.environ", {"PAYSTACK_SECRET_KEY": ""})
    def test_tenant_payment_error_does_not_expose_server_configuration(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("tenant-checkout", args=[self.tenant.pk]), follow=True
        )
        self.assertContains(response, "Payment provider is unavailable.")
        self.assertNotContains(response, "PAYSTACK_SECRET_KEY")
        self.assertEqual(self.subscription.payments.count(), 0)

    @patch("saas.billing.api_request")
    def test_expired_payment_replay_cannot_start_stale_automatic_charges(self, api):
        past = timezone.now() - timedelta(days=30)
        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            trial_until=past, paid_until=past, customer_code="CUS_billing"
        )
        payment = BillingPayment.objects.create(
            subscription=self.subscription,
            plan=self.plan,
            reference="expired-auto",
            amount=19999,
            currency="NGN",
            domain="test",
            email="billing@example.test",
            auto_renew=True,
            status="paid",
            credited_until=past,
        )
        data = self.paid_data(payment)
        data["authorization"] = {"reusable": True, "authorization_code": "AUTH_example"}
        api.return_value = data
        verify_payment(payment.reference)
        self.assertEqual(api.call_count, 1)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.renewal_state, "none")

    def test_canceled_renewal_is_disabled_in_customer_checkout_form(self):
        TenantSubscription.objects.filter(pk=self.subscription.pk).update(
            provider_code="SUB_off", renewal_state="off"
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("tenant-billing", args=[self.tenant.pk]))
        self.assertTrue(response.context["form"].fields["auto_renew"].disabled)

    def test_enrollment_rejects_wrong_gateway_environment(self):
        tenant = Tenant.objects.create(
            company=make_company("Wrong billing environment"),
            slug="wrong-environment",
            status="active",
        )
        plan = BillingPlan.objects.create(
            name="Live only",
            amount=10000,
            currency="NGN",
            interval="monthly",
            provider_code="PLN_live_only",
            provider_domain="live",
            publication_state="published",
        )
        with self.assertRaises(BillingError):
            enroll(tenant.pk, plan.pk, trial_days=14, actor=self.operator)
        self.assertFalse(TenantSubscription.objects.filter(tenant=tenant).exists())


@override_settings(
    COMPANY_SCOPING_STRICT=True,
    COMPANY_SCOPED_PERMISSIONS=True,
    PAYSTACK_SECRET_KEY="sk_test_example",
)
class ConcurrentBillingTests(CompanyFilterTestMixin, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_verification_credits_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections

        self.clear_company_context()
        tenant = Tenant.objects.create(
            company=make_company("Concurrent billing"),
            slug="concurrent-billing",
            status="active",
        )
        plan = BillingPlan.objects.create(
            name="Concurrent",
            amount=10000,
            currency="NGN",
            interval="monthly",
            provider_code="PLN_concurrent",
            provider_domain="test",
            publication_state="published",
        )
        subscription = TenantSubscription.objects.create(
            tenant=tenant, plan=plan, trial_until=timezone.now()
        )
        payment = BillingPayment.objects.create(
            subscription=subscription,
            plan=plan,
            reference="concurrent-ref",
            amount=10000,
            currency="NGN",
            domain="test",
            email="concurrent@example.test",
            auto_renew=True,
        )
        barrier = Barrier(2)
        data = dict(
            reference=payment.reference,
            status="success",
            amount=10000,
            currency="NGN",
            domain="test",
            paid_at=timezone.now().isoformat(),
            customer=dict(email=payment.email, customer_code="CUS_concurrent", id=321),
            authorization=dict(reusable=True, authorization_code="AUTH_concurrent"),
        )

        def provider(*args, **kwargs):
            if args[1].startswith("transaction/"):
                barrier.wait(timeout=10)
                return data
            if args[0] == "POST":
                return dict(
                    subscription_code="SUB_concurrent",
                    email_token="private-token",
                    domain="test",
                    amount=10000,
                )
            return dict(
                subscription_code="SUB_concurrent",
                email_token="private-token",
                domain="test",
                amount=10000,
                status="active",
                customer={"customer_code": "CUS_concurrent"},
                plan={
                    "plan_code": "PLN_concurrent",
                    "amount": 10000,
                    "currency": "NGN",
                    "interval": "monthly",
                },
                next_payment_date=TenantSubscription.objects.get(
                    pk=subscription.pk
                ).paid_until.isoformat(),
            )

        def worker():
            close_old_connections()
            try:
                return verify_payment(payment.reference).status
            finally:
                close_old_connections()

        with patch("saas.billing.api_request", side_effect=provider) as api:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker) for _ in range(2)]
                self.assertEqual(
                    [f.result(timeout=30) for f in futures], ["paid", "paid"]
                )
        self.assertEqual(
            len([call for call in api.call_args_list if call.args[0] == "POST"]), 1
        )
        subscription.refresh_from_db()
        self.assertEqual(subscription.renewal_state, "active")
        self.assertLess(subscription.paid_until, timezone.now() + timedelta(days=32))
        self.assertEqual(subscription.notices.filter(kind="payment_success").count(), 1)
