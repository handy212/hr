from django.apps import apps
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from employee.models import EmployeeWorkInformation
from horilla.testkit import make_company, make_employee, make_user
from horilla.testkit.company import CompanyFilterTestMixin


@override_settings(COMPANY_SCOPING_STRICT=True)
class HistoryIsolationTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.a=make_company('History A');self.b=make_company('History B')
        self.ea=make_employee(company=self.a,email='history-a@test.example')
        self.eb=make_employee(company=self.b,email='history-b@test.example')
        self.wa=EmployeeWorkInformation.objects.unscoped().get(employee_id=self.ea)
        self.wb=EmployeeWorkInformation.objects.unscoped().get(employee_id=self.eb)
        # Fixture updates use SQL; explicitly save snapshots for both owners.
        self.wa.save();self.wb.save()

    def _login(self, company, *permission_actions):
        username = f"history_user_{company.pk}"
        user = make_user(username)
        make_employee(company=company, email=f"{username}@test.example", user=user)
        permissions = [
            Permission.objects.get(
                codename=f"{action}_employeeworkinformation",
                content_type__app_label="employee",
                content_type__model="employeeworkinformation",
            )
            for action in permission_actions
        ]
        user.user_permissions.add(*permissions)
        self.client.force_login(user)
        session = self.client.session
        session["selected_company"] = str(company.pk)
        session.save()
        return user

    def test_generic_history_requires_view_permission(self):
        self._login(self.a)
        response = self.client.get(
            reverse("generic-history", args=[self.wa.pk]),
            {"model": "employee.EmployeeWorkInformation"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 403)

    def test_generic_history_cannot_load_another_company_record(self):
        self._login(self.a, "view")
        url = reverse("generic-history", args=[self.wa.pk])
        own_response = self.client.get(
            url,
            {"model": "employee.EmployeeWorkInformation"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(own_response.status_code, 200)

        foreign_response = self.client.get(
            reverse("generic-history", args=[self.wb.pk]),
            {"model": "employee.EmployeeWorkInformation"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(foreign_response.status_code, 404)

    def test_history_revert_requires_change_permission(self):
        self._login(self.a, "view")
        self.set_company_context(self.a.pk)
        history = self.wa.history.first()
        url = reverse(
            "history-revert",
            args=[self.wa.pk, history.history_id],
        )
        response = self.client.post(
            f"{url}?model=employee.EmployeeWorkInformation",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 403)

    def test_history_revert_cannot_target_another_company_record(self):
        self.set_company_context(self.b.pk)
        history = self.wb.history.first()
        self.assertIsNotNone(history)
        self._login(self.a, "change")
        url = reverse(
            "history-revert",
            args=[self.wb.pk, history.history_id],
        )
        response = self.client.post(
            f"{url}?model=employee.EmployeeWorkInformation",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 404)

    def test_direct_historical_queries_are_company_scoped(self):
        model=apps.get_model('employee','HistoricalEmployeeWorkInformation')
        self.set_company_context(self.a.pk)
        ids=set(model.objects.values_list('employee_id',flat=True))
        self.assertIn(self.ea.pk,ids)
        self.assertNotIn(self.eb.pk,ids)

    def test_history_descriptor_and_most_recent_cannot_cross_company(self):
        self.set_company_context(self.a.pk)
        self.assertTrue(self.wa.history.exists())
        self.assertFalse(self.wb.history.exists())
        with self.assertRaises(EmployeeWorkInformation.DoesNotExist):
            self.wb.history.most_recent()

    def test_missing_context_is_empty_but_explicit_recovery_remains_available(self):
        model=apps.get_model('employee','HistoricalEmployeeWorkInformation')
        self.clear_company_context()
        self.assertEqual(model.objects.count(),0)
        self.assertGreater(model.objects.unscoped().count(),0)
