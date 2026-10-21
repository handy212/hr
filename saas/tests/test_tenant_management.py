"""Platform authorization, atomic provisioning, and tenant lifecycle behavior."""

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from base.models import Company, CompanyGroupAssignment
from saas.models import Tenant
from saas.services import provision_tenant
from employee.models import Employee, EmployeeWorkInformation
from horilla.horilla_middlewares import get_selected_company
from horilla.testkit import make_company, make_employee, make_user
from horilla.testkit.company import CompanyFilterTestMixin
from horilla_auth.models import HorillaUser


@override_settings(
    COMPANY_SCOPING_STRICT=True,
    COMPANY_SCOPED_PERMISSIONS=True,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class TenantManagementTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.clear_company_context()
        self.platform = make_user("tenant_operator", is_superuser=True)
        self.platform_company = make_company("Operator company")
        make_employee(
            company=self.platform_company,
            user=self.platform,
            email="operator@test.example",
        )
        self.payload = {
            "company_name": "Acme",
            "slug": "acme",
            "address": "1 Test Road",
            "country": "US",
            "state": "CA",
            "city": "Oakland",
            "zip": "90001",
            "admin_first_name": "Ada",
            "admin_last_name": "Admin",
            "admin_email": "ada@acme.example",
            "admin_phone": "9999999999",
            "admin_password": "Tenants!Secure42Blue",
            "status": "trial",
        }

    def test_impersonation_start_uses_tenant_identity_and_stop_restores_operator(self):
        tenant = self.provision()
        self.client.force_login(self.platform)
        response = self.client.post(reverse("tenant-impersonate", args=[tenant.pk]))
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), tenant.primary_admin_id
        )
        self.assertEqual(
            self.client.session["selected_company"], str(tenant.company_id)
        )
        # Tenant credentials cannot acquire platform access while impersonating.
        self.assertEqual(self.client.get(reverse("tenant-list")).status_code, 403)
        response = self.client.post(reverse("tenant-impersonation-stop"))
        self.assertRedirects(response, reverse("tenant-list"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.platform.pk)
        self.assertNotIn("tenant_impersonation", self.client.session)

    def test_impersonation_rejects_suspended_missing_admin_and_foreign_admin(self):
        tenant = self.provision()
        self.client.force_login(self.platform)
        foreign = make_user("foreign_admin")
        make_employee(
            company=self.platform_company, user=foreign, email="foreign@test.example"
        )
        for admin, status in [
            (None, "active"),
            (foreign, "active"),
            (self.platform, "active"),
            (tenant.primary_admin, "suspended"),
        ]:
            Tenant.objects.filter(pk=tenant.pk).update(
                primary_admin=admin, status=status
            )
            self.client.post(reverse("tenant-impersonate", args=[tenant.pk]))
            self.assertEqual(
                int(self.client.session["_auth_user_id"]), self.platform.pk
            )
            self.assertNotIn("tenant_impersonation", self.client.session)

    def test_impersonation_requires_post_and_superuser_and_csrf(self):
        from django.test import Client

        tenant = self.provision()
        self.client.force_login(self.platform)
        self.assertEqual(
            self.client.get(
                reverse("tenant-impersonate", args=[tenant.pk])
            ).status_code,
            405,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.platform)
        self.assertEqual(
            csrf_client.post(
                reverse("tenant-impersonate", args=[tenant.pk])
            ).status_code,
            403,
        )
        tenant.primary_admin.is_new_employee = False
        tenant.primary_admin.save(update_fields=["is_new_employee"])
        self.client.force_login(tenant.primary_admin)
        self.assertEqual(
            self.client.post(
                reverse("tenant-impersonate", args=[tenant.pk])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(reverse("tenant-impersonation-stop")).status_code, 403
        )

    def test_impersonation_revoked_operator_cannot_return_as_superuser(self):
        tenant = self.provision()
        self.client.force_login(self.platform)
        self.client.post(reverse("tenant-impersonate", args=[tenant.pk]))
        HorillaUser.objects.filter(pk=self.platform.pk).update(is_superuser=False)
        self.assertRedirects(
            self.client.post(reverse("tenant-impersonation-stop")),
            reverse("login"),
            fetch_redirect_response=False,
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_impersonation_suspension_returns_to_operator_and_company_switch_blocked(
        self,
    ):
        tenant = self.provision()
        self.client.force_login(self.platform)
        self.client.post(reverse("tenant-impersonate", args=[tenant.pk]))
        self.assertEqual(self.client.post("/update-selected-company").status_code, 403)
        self.assertEqual(self.client.get("/change-password/").status_code, 403)
        Tenant.objects.filter(pk=tenant.pk).update(status="suspended")
        self.assertRedirects(self.client.get("/"), reverse("tenant-list"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.platform.pk)

    def test_admin_assignment_choices_exclude_other_companies_and_superusers(self):
        from saas.forms import TenantUpdateForm

        tenant = self.provision()
        users = TenantUpdateForm(instance=tenant).fields["primary_admin"].queryset
        self.assertIn(tenant.primary_admin, users)
        self.assertNotIn(self.platform, users)

    def provision(self, **changes):
        return provision_tenant(data={**self.payload, **changes}, actor=self.platform)

    def test_platform_login_and_home_land_on_tenants_without_employee_profile(self):
        operator = make_user(
            "independent_operator", is_superuser=True, password="Operator!Strong42"
        )
        for target in ["", "?next=/dashboard/"]:
            self.client.logout()
            response = self.client.post(
                reverse("login") + target,
                {"username": operator.username, "password": "Operator!Strong42"},
            )
            self.assertRedirects(response, reverse("tenant-list"))
        self.assertRedirects(self.client.get("/"), reverse("tenant-list"))
        self.assertEqual(self.client.get(reverse("tenant-create")).status_code, 200)

    def test_platform_shell_has_no_hr_navigation_or_check_in(self):
        self.client.force_login(self.platform)
        response = self.client.get(reverse("tenant-list"))
        self.assertContains(response, 'class="platform-shell"')
        for unwanted in [
            'id="sidebar"',
            'id="mainHeader"',
            "Check in",
            "Company settings",
            "country.js",
            "htmx.min.js",
        ]:
            self.assertNotContains(response, unwanted)

    @override_settings(TWO_FACTORS_AUTHENTICATION=True)
    def test_platform_accounts_without_employee_cannot_bypass_two_factor(self):
        from django.utils import timezone

        operator = make_user("otp_operator", is_superuser=True)
        self.client.force_login(operator)
        self.assertRedirects(
            self.client.get(reverse("tenant-list")),
            reverse("two-factor"),
            fetch_redirect_response=False,
        )
        session = self.client.session
        session["otp_code"] = "123456"
        session["otp_code_timestamp"] = timezone.now().timestamp()
        session.save()
        self.assertContains(self.client.get(reverse("two-factor")), "Verification code")
        self.assertRedirects(
            self.client.post(reverse("two-factor"), {"otp": "123456"}),
            "/",
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.get(reverse("tenant-list")).status_code, 200)

    def test_tenant_details_and_update_company_fields(self):
        tenant = self.provision()
        self.client.force_login(self.platform)
        self.assertContains(
            self.client.get(reverse("tenant-detail", args=[tenant.pk])),
            self.payload["admin_email"],
        )
        data = {
            key: self.payload[key]
            for key in [
                "company_name",
                "slug",
                "status",
                "address",
                "country",
                "state",
                "city",
                "zip",
            ]
        }
        data.update(
            company_name="Updated Acme",
            slug="updated-acme",
            status="active",
            city="Berkeley",
        )
        response = self.client.post(reverse("tenant-edit", args=[tenant.pk]), data)
        self.assertRedirects(response, reverse("tenant-detail", args=[tenant.pk]))
        tenant.refresh_from_db()
        self.assertEqual(
            (tenant.slug, tenant.status, tenant.company.company, tenant.company.city),
            ("updated-acme", "active", "Updated Acme", "Berkeley"),
        )
        other = self.provision(
            company_name="Other", slug="other", admin_email="other@acme.example"
        )
        data.update(slug=other.slug, company_name="Must not save")
        self.assertEqual(
            self.client.post(
                reverse("tenant-edit", args=[tenant.pk]), data
            ).status_code,
            200,
        )
        tenant.company.refresh_from_db()
        self.assertEqual(tenant.company.company, "Updated Acme")

    def test_delete_requires_confirmation_hides_account_and_retains_hr_data(self):
        tenant = self.provision()
        company_id = tenant.company_id
        self.client.force_login(self.platform)
        url = reverse("tenant-delete", args=[tenant.pk])
        self.assertContains(self.client.get(url), "HR records will be retained")
        self.assertEqual(
            self.client.post(url, {"confirm_slug": "wrong"}).status_code, 400
        )
        tenant.refresh_from_db()
        self.assertIsNone(tenant.deleted_at)
        self.assertRedirects(
            self.client.post(url, {"confirm_slug": tenant.slug}), reverse("tenant-list")
        )
        tenant.refresh_from_db()
        self.assertIsNotNone(tenant.deleted_at)
        self.assertEqual(tenant.status, "suspended")
        self.assertTrue(Company.objects.filter(pk=company_id).exists())
        self.assertTrue(
            EmployeeWorkInformation.objects.unscoped()
            .filter(company_id=company_id)
            .exists()
        )
        self.assertNotContains(
            self.client.get(reverse("tenant-list")), tenant.primary_admin.email
        )
        for route in ["tenant-detail", "tenant-edit", "tenant-delete"]:
            self.assertEqual(
                self.client.get(reverse(route, args=[tenant.pk])).status_code, 404
            )
        self.assertEqual(
            self.client.post(
                reverse("tenant-status", args=[tenant.pk]), {"status": "active"}
            ).status_code,
            404,
        )

        from rest_framework_simplejwt.tokens import RefreshToken

        token = str(RefreshToken.for_user(tenant.primary_admin).access_token)
        Tenant.objects.filter(pk=tenant.pk).update(status="active")
        self.client.logout()
        response = self.client.get(
            "/api/v1/employee/list/employees/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 403)

    def test_platform_mail_identity_does_not_require_an_employee(self):
        from types import SimpleNamespace
        from django.contrib.auth.models import AnonymousUser
        from base.backends import ConfiguredEmailBackend
        from base.models import DynamicEmailConfiguration

        config = DynamicEmailConfiguration.objects.unscoped().create(
            username="platform",
            host="smtp.example.test",
            from_email="platform@example.test",
            display_name="Platform",
            is_primary=True,
        )
        operator = make_user("mail_operator", is_superuser=True)
        from base.backends import _thread_locals

        previous_request = _thread_locals.request
        try:
            for user in [operator, AnonymousUser()]:
                _thread_locals.request = SimpleNamespace(user=user)
                self.assertEqual(
                    ConfiguredEmailBackend.get_dynamic_email_config().pk, config.pk
                )
        finally:
            _thread_locals.request = previous_request

    def test_edit_and_delete_are_csrf_protected(self):
        from django.test import Client

        tenant = self.provision()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.platform)
        for route in ["tenant-edit", "tenant-delete"]:
            self.assertEqual(
                client.post(
                    reverse(route, args=[tenant.pk]), {"confirm_slug": tenant.slug}
                ).status_code,
                403,
            )
        tenant.refresh_from_db()
        self.assertIsNone(tenant.deleted_at)

    def test_tenant_users_cannot_edit_or_delete_accounts(self):
        tenant = self.provision()
        user = tenant.primary_admin
        user.is_new_employee = False
        user.save()
        self.client.force_login(user)
        for route in ["tenant-detail", "tenant-edit", "tenant-delete"]:
            url = reverse(route, args=[tenant.pk])
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(
                self.client.post(url, {"confirm_slug": tenant.slug}).status_code, 403
            )

    def test_repeated_provisioning_keeps_single_global_attendance_default(self):
        from attendance.models import AttendanceGeneralSetting

        self.provision()
        self.provision(
            company_name="Second", slug="second", admin_email="second@acme.example"
        )
        self.assertEqual(
            AttendanceGeneralSetting.objects.unscoped().filter(company_id=None).count(),
            1,
        )
        self.client.force_login(self.platform)
        response = self.client.post(
            reverse("tenant-create"),
            {
                **self.payload,
                "company_name": "Third",
                "slug": "third",
                "admin_email": "third@acme.example",
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_existing_duplicate_attendance_defaults_do_not_block_provisioning(self):
        from attendance.models import AttendanceGeneralSetting

        AttendanceGeneralSetting.objects.unscoped().create(company_id=None)
        tenant = self.provision()
        self.assertTrue(Tenant.objects.filter(pk=tenant.pk).exists())
        self.assertEqual(
            AttendanceGeneralSetting.objects.unscoped().filter(company_id=None).count(),
            2,
        )

    def test_provisioning_links_company_admin_defaults_and_role(self):
        tenant = self.provision()
        admin = tenant.primary_admin
        self.assertEqual(tenant.slug, "acme")
        self.assertTrue(admin.check_password(self.payload["admin_password"]))
        self.assertFalse(admin.is_superuser)
        self.assertFalse(admin.is_staff)
        self.assertTrue(admin.is_new_employee)
        self.assertEqual(
            admin.employee_get.employee_work_info.company_id_id, tenant.company_id
        )
        self.assertTrue(
            CompanyGroupAssignment.objects.filter(
                user=admin, company=tenant.company
            ).exists()
        )
        self.set_company_context(tenant.company_id)
        self.assertTrue(tenant.company.defaultexportpermission_set.exists())
        self.assertTrue(admin.has_perm("employee.add_employee"))
        self.assertFalse(admin.has_perm("base.change_company"))
        self.assertFalse(admin.has_perm("base.change_dynamicemailconfiguration"))
        self.set_company_context(self.platform_company.pk)
        # Permission caches belong to a resolved request/company.
        admin = HorillaUser.objects.get(pk=admin.pk)
        self.assertFalse(admin.has_perm("employee.add_employee"))

    def test_duplicate_slug_does_not_create_partial_company_or_user(self):
        self.provision()
        counts = (Company.objects.count(), HorillaUser.objects.count())
        with self.assertRaises(ValidationError):
            self.provision(company_name="Duplicate", admin_email="other@acme.example")
        self.assertEqual((Company.objects.count(), HorillaUser.objects.count()), counts)

    def test_failure_rolls_back_every_created_record_and_restores_scope(self):
        counts = (
            Company.objects.count(),
            HorillaUser.objects.count(),
            Employee.objects.unscoped().count(),
        )
        self.set_company_context(self.platform_company.pk)
        with patch(
            "saas.services.CompanyGroupAssignment.objects.create",
            side_effect=RuntimeError("role failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.provision()
        self.assertEqual(get_selected_company(), self.platform_company.pk)
        self.assertEqual(
            (
                Company.objects.count(),
                HorillaUser.objects.count(),
                Employee.objects.unscoped().count(),
            ),
            counts,
        )
        self.assertFalse(Tenant.objects.filter(slug="acme").exists())

    def test_weak_password_and_reused_email_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.provision(admin_password="123")
        self.provision()
        with self.assertRaises(ValidationError):
            self.provision(
                slug="other", company_name="Other", admin_email="ADA@ACME.EXAMPLE"
            )

    def test_provisioning_requires_platform_actor_and_scoped_permissions(self):
        with self.assertRaises(PermissionError):
            provision_tenant(data=self.payload, actor=make_user("ordinary_operator"))
        with override_settings(COMPANY_SCOPED_PERMISSIONS=False):
            with self.assertRaises(ValidationError):
                self.provision()

    def test_platform_can_create_list_and_change_status(self):
        self.client.force_login(self.platform)
        response = self.client.post(reverse("tenant-create"), self.payload)
        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(slug="acme")
        response = self.client.get(reverse("tenant-list"))
        self.assertContains(response, "Acme")
        self.assertContains(response, "ada@acme.example")
        response = self.client.post(
            reverse("tenant-status", args=[tenant.pk]), {"status": "suspended"}
        )
        self.assertEqual(response.status_code, 302)
        tenant.refresh_from_db()
        self.assertEqual(tenant.status, "suspended")
        response = self.client.post(
            reverse("tenant-status", args=[tenant.pk]), {"status": "invalid"}
        )
        self.assertEqual(response.status_code, 400)
        tenant.refresh_from_db()
        self.assertEqual(tenant.status, "suspended")

    def test_regular_staff_and_tenant_admin_cannot_access_platform_routes(self):
        tenant = self.provision()
        for user in [make_user("plain_user"), tenant.primary_admin]:
            make_staff = user.pk == tenant.primary_admin_id
            user.is_staff = make_staff
            user.is_new_employee = False
            user.save()
            if not hasattr(user, "employee_get"):
                make_employee(
                    company=self.platform_company,
                    user=user,
                    email=f"{user.username}@test.example",
                )
            self.client.force_login(user)
            for url in [reverse("tenant-list"), reverse("tenant-create")]:
                self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(
                self.client.post(reverse("tenant-create"), self.payload).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    reverse("tenant-status", args=[tenant.pk]), {"status": "suspended"}
                ).status_code,
                403,
            )

    def test_status_is_post_only_and_csrf_protected(self):
        from django.test import Client

        tenant = self.provision()
        self.client.force_login(self.platform)
        self.assertEqual(
            self.client.get(reverse("tenant-status", args=[tenant.pk])).status_code, 405
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.platform)
        self.assertEqual(
            csrf_client.post(
                reverse("tenant-status", args=[tenant.pk]), {"status": "suspended"}
            ).status_code,
            403,
        )

    def test_suspension_blocks_session_and_existing_jwt_and_reactivation_restores_access(
        self,
    ):
        from rest_framework_simplejwt.tokens import RefreshToken

        tenant = self.provision()
        user = tenant.primary_admin
        user.is_new_employee = False
        user.save()
        token = str(RefreshToken.for_user(user).access_token)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("tenant-list")).status_code, 403)
        Tenant.objects.filter(pk=tenant.pk).update(status="suspended")
        self.assertEqual(self.client.get("/").status_code, 403)
        self.client.logout()
        response = self.client.get(
            "/api/v1/employee/list/employees/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("suspended", str(response.content).lower())
        Tenant.objects.filter(pk=tenant.pk).update(status="active")
        response = self.client.get(
            "/api/v1/employee/list/employees/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(user.email, str(response.content))
        self.assertNotIn("operator@test.example", str(response.content))

    def test_platform_can_manage_a_suspended_selected_company(self):
        tenant = self.provision(status="suspended")
        self.client.force_login(self.platform)
        session = self.client.session
        session["selected_company"] = str(tenant.company_id)
        session.save()
        self.assertEqual(self.client.get(reverse("tenant-list")).status_code, 200)

    def test_backfill_preserves_legacy_companies_and_is_repeatable(self):
        from importlib import import_module
        from django.db import connection
        from django.db.migrations.loader import MigrationLoader

        apps = MigrationLoader(connection).project_state(
            [("base", "0018_tenant_deleted_at")]
        ).apps
        backfill = import_module("base.migrations.0017_tenant").backfill_tenants
        backfill(apps, None)
        backfill(apps, None)
        tenant = Tenant.objects.get(company=self.platform_company)
        self.assertEqual(tenant.slug, f"company-{self.platform_company.pk}")
        self.assertEqual(tenant.status, "active")
        self.assertIsNone(tenant.primary_admin)

    def test_platform_login_required_and_form_errors_do_not_echo_password(self):
        self.assertEqual(self.client.get(reverse("tenant-list")).status_code, 302)
        self.client.force_login(self.platform)
        response = self.client.get(reverse("tenant-create"))
        self.assertContains(response, 'type="password"')
        self.assertContains(response, 'for="tenant_country"')
        self.assertContains(response, 'id="tenant_country"')
        self.assertNotContains(response, 'id="id_country"')
        response = self.client.post(
            reverse("tenant-create"),
            {**self.payload, "admin_password": "TooShort42"[:4]},
        )
        self.assertContains(response, "Please correct the errors below.")
        self.assertNotContains(response, 'value="TooS"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(
            response,
            'aria-describedby="tenant_admin_password_helptext tenant_admin_password_error"',
        )
        self.assertFalse(Tenant.objects.filter(slug="acme").exists())

    def test_search_status_filter_counts_and_pagination(self):
        tenant = self.provision()
        self.client.force_login(self.platform)
        response = self.client.get(
            reverse("tenant-list"), {"q": "acme", "status": "trial"}
        )
        self.assertEqual(response.context["page_obj"][0].pk, tenant.pk)
        self.assertEqual(response.context["page_obj"][0].employee_count, 1)
        self.assertEqual(response.context["trial_count"], 1)
        self.assertNotContains(
            self.client.get(reverse("tenant-list"), {"status": "active"}),
            "ada@acme.example",
        )
        companies = Company.objects.bulk_create(
            [Company(company=f"Page {i}") for i in range(26)]
        )
        Tenant.objects.bulk_create(
            [
                Tenant(company=company, slug=f"page-{i}", status="active")
                for i, company in enumerate(companies)
            ]
        )
        response = self.client.get(reverse("tenant-list"), {"page": 2})
        self.assertEqual(response.context["page_obj"].paginator.count, 27)
        self.assertEqual(len(response.context["page_obj"]), 2)

    def test_all_company_scope_denies_suspended_tenant_but_switching_remains_available(
        self,
    ):
        tenant = self.provision()
        admin = tenant.primary_admin
        admin.is_new_employee = False
        admin.save()
        other_company = make_company("Active secondary")
        Tenant.objects.create(company=other_company, slug="secondary", status="active")
        group = CompanyGroupAssignment.objects.get(
            user=admin, company=tenant.company
        ).group
        CompanyGroupAssignment.objects.create(
            user=admin, company=other_company, group=group
        )
        Tenant.objects.filter(pk=tenant.pk).update(status="suspended")
        self.client.force_login(admin)
        session = self.client.session
        session["selected_company"] = "all"
        session.save()
        self.assertEqual(self.client.get("/").status_code, 403)
        response = self.client.get(
            reverse("update-selected-company"),
            {"company_id": other_company.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertNotEqual(response.status_code, 403)
        self.assertEqual(self.client.session["selected_company"], str(other_company.pk))
        self.assertNotEqual(self.client.get("/").status_code, 403)
