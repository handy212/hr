from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from attendance.models import AttendanceGeneralSetting
from base.models import CompanyGroupAssignment
from horilla.testkit import make_company, make_employee, make_user


@override_settings(COMPANY_SCOPED_PERMISSIONS=True)
class TimeRunnerCompanyScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company_a = make_company("Timerunner A")
        cls.company_b = make_company(
            "Timerunner B",
            address="2 Timerunner St",
            city="SF",
            zip="94105",
        )
        cls.company_c = make_company(
            "Timerunner C",
            address="3 Timerunner St",
            city="NY",
            zip="10001",
        )
        cls.user = make_user("timerunner_multi_company")
        make_employee(
            company=cls.company_a,
            email="timerunner-multi-company@example.test",
            user=cls.user,
        )
        group = Group.objects.create(name="Timerunner Company Admin")
        CompanyGroupAssignment.objects.create(
            user=cls.user, company=cls.company_a, group=group
        )
        CompanyGroupAssignment.objects.create(
            user=cls.user, company=cls.company_b, group=group
        )
        permission = Permission.objects.get(
            content_type__app_label="attendance",
            codename="add_attendancegeneralsetting",
        )
        cls.user.user_permissions.add(permission)
        AttendanceGeneralSetting.objects.unscoped().bulk_create(
            [
                AttendanceGeneralSetting(company_id=cls.company_a, time_runner=True),
                AttendanceGeneralSetting(company_id=cls.company_c, time_runner=True),
                AttendanceGeneralSetting(company_id=None, time_runner=True),
            ]
        )

    def test_all_companies_toggle_updates_only_assigned_companies(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["selected_company"] = "all"
        session.save()

        response = self.client.get(
            reverse("enable-timerunner"), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        settings_by_company = {
            setting.company_id_id: setting.time_runner
            for setting in AttendanceGeneralSetting.objects.unscoped().all()
        }
        self.assertFalse(settings_by_company[self.company_a.pk])
        company_b_setting = AttendanceGeneralSetting.objects.unscoped().get(
            company_id=self.company_b
        )
        self.assertFalse(company_b_setting.time_runner)
        self.assertTrue(settings_by_company[self.company_c.pk])
        self.assertTrue(settings_by_company[None])

    def test_all_companies_toggle_without_scope_is_forbidden(self):
        user = make_user("timerunner_unassigned")
        employee = make_employee(
            company=self.company_a,
            email="timerunner-unassigned@example.test",
            user=user,
        )
        employee.employee_work_info.company_id = None
        employee.employee_work_info.save(update_fields=["company_id"])
        permission = Permission.objects.get(
            content_type__app_label="attendance",
            codename="add_attendancegeneralsetting",
        )
        user.user_permissions.add(permission)
        self.client.force_login(user)
        session = self.client.session
        session["selected_company"] = "all"
        session.save()

        response = self.client.get(
            reverse("enable-timerunner"), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            all(
                AttendanceGeneralSetting.objects.unscoped().values_list(
                    "time_runner", flat=True
                )
            )
        )

    def test_platform_superuser_can_toggle_all_company_settings(self):
        user = make_user("platform_timerunner", is_superuser=True)
        make_employee(
            company=self.company_a,
            email="platform-timerunner@example.test",
            user=user,
        )
        self.client.force_login(user)
        session = self.client.session
        session["selected_company"] = "all"
        session.save()

        response = self.client.get(
            reverse("enable-timerunner"), HTTP_HX_REQUEST="true"
        )

        self.assertTrue(user.is_superuser)
        self.assertTrue(
            response.wsgi_request.user.is_superuser,
            f"request user {response.wsgi_request.user.pk} is not {user.pk}",
        )
        self.assertEqual(response.status_code, 200, response.content[:1000])
        self.assertFalse(
            any(
                AttendanceGeneralSetting.objects.unscoped().values_list(
                    "time_runner", flat=True
                )
            )
        )
