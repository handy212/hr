"""Regression checks for request views that previously bypassed tenant scoping."""

from django.test import TestCase, override_settings
from django.urls import reverse

from base.models import Company
from horilla.testkit import make_employee
from horilla_auth.models import HorillaUser
from offboarding.models import Offboarding, OffboardingEmployee
from recruitment.models import Recruitment


@override_settings(
    COMPANY_SCOPING_STRICT=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class SaaSViewIsolationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(company="View Tenant A")
        self.other_company = Company.objects.create(company="View Tenant B")
        self.user = HorillaUser.objects.create_superuser(
            username="viewadmin", email="viewadmin@example.com", password="pass"
        )
        make_employee(company=self.company, email="viewadmin@example.com", user=self.user)
        self.other_employee = make_employee(
            company=self.other_company, email="other@example.com"
        )
        self.other_recruitment = Recruitment.objects.unscoped().create(
            title="Other tenant recruitment", company_id=self.other_company
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["selected_company"] = str(self.company.pk)
        session.save()

    def test_employee_profile_cannot_open_other_tenant(self):
        response = self.client.get(
            reverse("profile-new", kwargs={"pk": self.other_employee.pk})
        )
        self.assertEqual(response.status_code, 302)

    def test_legacy_employee_detail_cannot_open_other_tenant(self):
        response = self.client.get(
            reverse("employee-view-individual", kwargs={"obj_id": self.other_employee.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_employee_update_cannot_move_other_tenant_employee(self):
        response = self.client.post(
            reverse("employee-view-update", kwargs={"obj_id": self.other_employee.pk}),
            {"form": "work"},
        )
        self.assertEqual(response.status_code, 302)
        self.other_employee.employee_work_info.refresh_from_db()
        self.assertEqual(
            self.other_employee.employee_work_info.company_id, self.other_company
        )

    def test_recruitment_shell_cannot_open_other_tenant(self):
        response = self.client.get(
            reverse(
                "recruitment-pipeline-shell",
                kwargs={"rec_id": self.other_recruitment.pk},
            )
        )
        self.assertEqual(response.status_code, 302)

    def test_offboarding_chart_excludes_other_tenant(self):
        process = Offboarding.objects.unscoped().create(
            title="Other exit", description="Other tenant", company_id=self.other_company
        )
        archived = process.offboardingstage_set.get(type="archived")
        OffboardingEmployee.objects.unscoped().create(
            employee_id=self.other_employee, stage_id=archived
        )

        response = self.client.get(reverse("dashboard-join-chart"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][:2], [0, 0])
