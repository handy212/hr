"""Commercial feature access is independent of HR permissions and ownership."""

from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from horilla.testkit import make_company, make_user, make_employee
from horilla.testkit.company import CompanyFilterTestMixin
from saas.models import BillingPlan, BillingPlanFeature, Tenant, TenantSubscription
from saas.features import all_features, set_plan_features, company_has_feature
from saas.plan_forms import BillingPlanForm


@override_settings(
    COMPANY_SCOPING_STRICT=True,
    COMPANY_SCOPED_PERMISSIONS=True,
    TWO_FACTORS_AUTHENTICATION=False,
)
class EntitlementTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.clear_company_context()
        self.company = make_company("Feature account")
        self.user = make_user("features_admin")
        make_employee(
            company=self.company, user=self.user, email="features@example.test"
        )
        self.tenant = Tenant.objects.create(
            company=self.company,
            slug="features",
            status="active",
            primary_admin=self.user,
        )
        self.plan = BillingPlan.objects.create(
            name="Core", amount=10000, currency="NGN", interval="monthly"
        )
        self.subscription = TenantSubscription.objects.create(
            tenant=self.tenant,
            plan=self.plan,
            trial_until=timezone.now() + timedelta(days=14),
        )

    def test_defaults_preserve_features_and_legacy_access(self):
        self.assertEqual(
            set(self.plan.entitlements.values_list("feature", flat=True)),
            set(all_features()),
        )
        self.assertTrue(company_has_feature(self.company.pk, "payroll"))
        legacy = make_company("Unenrolled")
        self.assertTrue(company_has_feature(legacy.pk, "payroll"))

    def test_dependencies_and_unknown_features_are_rejected(self):
        for features in [["payroll"], ["devices"], ["onboarding"], ["invented"]]:
            with self.assertRaises(ValidationError):
                set_plan_features(self.plan, features)
        set_plan_features(self.plan, [])
        self.assertFalse(company_has_feature(self.company.pk, "payroll"))

    def test_published_features_are_frozen(self):
        set_plan_features(self.plan, ["attendance"])
        BillingPlan.objects.filter(pk=self.plan.pk).update(
            publication_state="published"
        )
        with self.assertRaises(ValidationError):
            set_plan_features(self.plan, [])
        with self.assertRaises(ValidationError), transaction.atomic():
            self.plan.entitlements.all().delete()
        with self.assertRaises(ValidationError), transaction.atomic():
            BillingPlanFeature.objects.create(plan=self.plan, feature="leave")

    def test_published_feature_cannot_be_moved_to_another_plan(self):
        feature = self.plan.entitlements.get(feature="attendance")
        draft = BillingPlan.objects.create(
            name="Other draft", amount=10000, currency="NGN", interval="monthly"
        )
        set_plan_features(draft, [])
        BillingPlan.objects.filter(pk=self.plan.pk).update(
            publication_state="published"
        )
        feature.plan = draft
        with self.assertRaises(ValidationError), transaction.atomic():
            feature.save()
        feature.refresh_from_db()
        self.assertEqual(feature.plan_id, self.plan.pk)

    def test_form_saves_selected_features_without_implicit_grants(self):
        form = BillingPlanForm(
            data=dict(
                name="Basic",
                price="99.99",
                currency="NGN",
                interval="monthly",
                active="on",
                features=["attendance"],
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        plan = form.save()
        self.assertEqual(
            list(plan.entitlements.values_list("feature", flat=True)), ["attendance"]
        )

    def test_commit_false_persists_explicit_features_with_save_m2m(self):
        form = BillingPlanForm(
            data=dict(
                name="Foundation",
                price="9.99",
                currency="NGN",
                interval="monthly",
                active="on",
                features=[],
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        plan = form.save(commit=False)
        self.assertIsNone(plan.pk)
        plan.save()
        form.save_m2m()
        self.assertFalse(plan.entitlements.exists())

    @override_settings(PAYSTACK_SECRET_KEY="sk_test_features")
    def test_publication_rejects_invalid_dependency_before_provider_request(self):
        from unittest.mock import patch
        from saas.paystack import publish_plan, PaystackError

        operator = make_user("feature_operator", is_superuser=True)
        self.plan.entitlements.exclude(feature="payroll").delete()
        with patch("saas.paystack.api_request") as gateway:
            with self.assertRaises(PaystackError):
                publish_plan(self.plan.pk, actor=operator)
        gateway.assert_not_called()
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.publication_state, "draft")

    def test_model_reads_and_history_are_hidden_without_deleting_data(self):
        from attendance.models import Attendance
        from horilla.testkit.factories import make_attendance
        from employee.models import Employee

        employee = Employee.objects.unscoped().get(employee_user_id=self.user.pk)
        attendance = make_attendance(
            employee=employee, attendance_date=timezone.localdate()
        )
        set_plan_features(self.plan, [])
        self.set_company_context(self.company.pk)
        self.assertFalse(Attendance.objects.filter(pk=attendance.pk).exists())
        self.assertTrue(Attendance.objects.unscoped().filter(pk=attendance.pk).exists())
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())
        if hasattr(Attendance, "history"):
            self.assertFalse(Attendance.history.filter(id=attendance.pk).exists())

    def test_web_module_route_denies_access_even_with_hr_permission(self):
        from django.urls import reverse
        from django.contrib.auth.models import Permission

        self.user.user_permissions.add(
            Permission.objects.get(
                codename="view_attendance", content_type__app_label="attendance"
            )
        )
        set_plan_features(self.plan, [])
        self.client.force_login(self.user)
        response = self.client.get(reverse("attendance-view"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "not included in your plan", status_code=403)
        self.assertEqual(
            self.client.get(
                reverse("tenant-billing", args=[self.tenant.pk])
            ).status_code,
            200,
        )

    def test_jwt_module_access_is_denied(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        from django.urls import reverse

        set_plan_features(self.plan, [])
        token = RefreshToken.for_user(self.user).access_token
        response = self.client.get(
            reverse("api-check-in"), HTTP_AUTHORIZATION="Bearer " + str(token)
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("not included", str(response.content))

    def test_device_ingress_is_denied_before_heartbeat(self):
        from device_gateway.models import TerminalDevice

        set_plan_features(self.plan, [])
        device = TerminalDevice.all_objects.create(
            company_id=self.company, sn="FEATURE-DEVICE"
        )
        response = self.client.post(
            "/api/terminal/Device/Keepalive",
            {"SN": device.sn},
            content_type="application/json",
        )
        self.assertEqual(response.json()["Success"], 401)
        device.refresh_from_db()
        self.assertIsNone(device.last_seen)

    def test_public_recruitment_excludes_unlicensed_account(self):
        from horilla.testkit.factories import make_recruitment
        from django.urls import reverse

        recruitment = make_recruitment(
            company=self.company, title="Unlicensed public job"
        )
        recruitment.is_published = True
        recruitment.save(update_fields=["is_published"])
        set_plan_features(self.plan, [])
        self.assertNotContains(
            self.client.get(reverse("open-recruitments")), recruitment.title
        )
        response = self.client.get(
            reverse("application-form"), {"recruitmentId": recruitment.pk}
        )
        self.assertEqual(response.status_code, 404)

    def test_report_requires_both_reports_and_source_module(self):
        from report.registry import ReportDefinition
        from saas.features import report_features_allowed

        definition = ReportDefinition(
            slug="features-report",
            name="Attendance",
            domain="time_leave",
            description="",
            permission="attendance.view_attendance",
            query_fn=lambda _: {},
            required_apps=("attendance",),
        )
        set_plan_features(self.plan, ["reports"])
        self.assertFalse(
            report_features_allowed(definition, company_ids=[self.company.pk])
        )
        set_plan_features(self.plan, ["reports", "attendance"])
        self.assertTrue(
            report_features_allowed(definition, company_ids=[self.company.pk])
        )

    def test_mixed_company_reads_filter_owners_and_actions_require_every_company(self):
        from types import SimpleNamespace
        from attendance.models import Attendance
        from horilla.testkit.factories import make_attendance
        from employee.models import Employee
        from horilla.horilla_middlewares import _thread_locals
        from saas.features import scope_has_features

        employee = Employee.objects.unscoped().get(employee_user_id=self.user.pk)
        own = make_attendance(employee=employee, attendance_date=timezone.localdate())
        other_company = make_company("Other entitled company")
        other_employee = make_employee(
            company=other_company, email="other-feature@example.test"
        )
        other = make_attendance(
            employee=other_employee, attendance_date=timezone.localdate()
        )
        set_plan_features(self.plan, [])
        request = SimpleNamespace(
            user=self.user, all_my_company_ids=[self.company.pk, other_company.pk]
        )
        previous = getattr(_thread_locals, "request", None)
        _thread_locals.request = request
        try:
            self.set_company_context("all")
            self.assertEqual(
                list(Attendance.objects.values_list("pk", flat=True)), [other.pk]
            )
            self.assertFalse(scope_has_features(("attendance",), request=request))
            self.set_company_context(other_company.pk)
            self.assertTrue(scope_has_features(("attendance",), request=request))
            self.set_company_context(self.company.pk)
            self.assertFalse(Attendance.objects.filter(pk=own.pk).exists())
            self.assertFalse(scope_has_features(("attendance",), request=request))
        finally:
            _thread_locals.request = previous

    def test_forced_report_delivery_does_not_override_feature_access(self):
        from unittest.mock import patch
        from report.delivery import deliver_subscription
        from report.models import ReportSubscription
        from report.registry import ReportDefinition

        definition = ReportDefinition(
            slug="feature-report",
            name="Feature report",
            domain="workforce",
            description="",
            permission="employee.view_employee",
            query_fn=lambda _: {},
        )
        subscription = ReportSubscription.objects.unscoped().create(
            company_id=self.company,
            owner=self.user,
            name="Feature test",
            report_slug=definition.slug,
        )
        set_plan_features(self.plan, [])
        with (
            patch("report.delivery.get_report", return_value=definition),
            patch("report.delivery.run_report") as run,
        ):
            result = deliver_subscription(subscription, force=True)
        self.assertEqual(result.status, "feature_disabled")
        run.assert_not_called()

    def test_employee_based_attendance_job_does_not_create_unlicensed_records(self):
        from attendance.scheduler import create_work_record
        from attendance.models import WorkRecords

        set_plan_features(self.plan, [])
        self.set_company_context(self.company.pk)
        create_work_record()
        self.assertFalse(
            WorkRecords.objects.unscoped()
            .filter(employee_id__employee_user_id=self.user.pk)
            .exists()
        )

    def test_core_dashboard_module_route_and_mobile_hints_respect_features(self):
        from django.urls import reverse
        from horilla_api.api_methods.base.capabilities import build_capabilities

        set_plan_features(self.plan, [])
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard-attendance-trend"))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["feature_disabled"])
        self.assertFalse(build_capabilities(self.user)["features"]["attendance"])
        self.assertFalse(build_capabilities(self.user)["features"]["payroll"])
