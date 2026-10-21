"""Stage choices and moves must stay within the selected company."""

from django.test import TestCase, override_settings
from django.urls import reverse

from horilla.testkit import make_company, make_employee, make_user
from horilla.testkit.company import CompanyFilterTestMixin
from offboarding.models import Offboarding, OffboardingEmployee, OffboardingStage


@override_settings(
    COMPANY_SCOPING_STRICT=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class StageTenantIsolationTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.clear_company_context()
        self.company = make_company("Stage Tenant A")
        self.other_company = make_company("Stage Tenant B")
        self.admin = make_user("stage_admin", is_superuser=True)
        make_employee(company=self.company, user=self.admin, email="admin@stage.test")
        self.employee = make_employee(company=self.company, email="leaver@stage.test")
        self.process = Offboarding.objects.create(
            title="Tenant A exits", description="A", company_id=self.company
        )
        self.other_process = Offboarding.objects.create(
            title="Tenant B exits", description="B", company_id=self.other_company
        )
        # Creation returns instances even without a read context.
        self.stage = OffboardingStage.objects.create(
            title="A notice", type="notice_period", offboarding_id=self.process
        )
        self.other_stage = OffboardingStage.objects.create(
            title="B archive", type="archived", offboarding_id=self.other_process
        )
        self.record = OffboardingEmployee.objects.create(
            employee_id=self.employee, stage_id=self.stage
        )
        self.client.force_login(self.admin)
        session = self.client.session
        session["selected_company"] = str(self.company.pk)
        session.save()

    def test_stage_choices_exclude_other_company(self):
        self.set_company_context(self.company.pk)
        self.assertTrue(OffboardingStage.objects.filter(pk=self.stage.pk).exists())
        self.assertFalse(
            OffboardingStage.objects.filter(pk=self.other_stage.pk).exists()
        )

    def test_stage_reads_without_context_fail_closed(self):
        self.assertFalse(OffboardingStage.objects.exists())

    def test_move_cannot_target_other_company_stage(self):
        response = self.client.get(
            reverse("offboarding-change-stage"),
            {"employee_ids": [self.record.pk], "stage_id": self.other_stage.pk},
            HTTP_HX_REQUEST="true",
        )
        self.record.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(self.record.stage_id_id, self.stage.pk)
        self.assertTrue(self.employee.is_active)
        self.assertEqual(response.status_code, 200)
