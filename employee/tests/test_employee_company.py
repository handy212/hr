"""Employee create + company isolation smoke tests."""

import io

from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from employee.cbv.employees import EmployeesList
from employee.models import Employee, EmployeeWorkInformation
from horilla.testkit import (
    CompanyFilterTestMixin,
    make_company,
    make_employee,
    make_user,
)
from horilla_views.cbv_methods import get_verbose_name_from_field_path



class EmployeeCreateTests(TestCase):
    def test_create_employee_creates_user_and_work_info(self):
        company = make_company("Emp Co")
        emp = make_employee(
            company=company,
            email="newhire@test.horilla",
            first_name="New",
            last_name="Hire",
            phone="9111111111",
        )
        self.assertIsNotNone(emp.pk)
        self.assertTrue(hasattr(emp, "employee_work_info"))
        self.assertEqual(emp.employee_work_info.company_id_id, company.pk)
        self.assertIsNotNone(emp.employee_user_id_id)
        self.assertEqual(emp.employee_user_id.email, "newhire@test.horilla")


class EmployeeCompanyIsolationTests(CompanyFilterTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company_a = make_company("Company A")
        cls.company_b = make_company(
            "Company B",
            address="2 Other St",
            city="SF",
            zip="94105",
        )
        cls.emp_a = make_employee(
            company=cls.company_a,
            email="a@emp.test",
            first_name="Ann",
            last_name="A",
        )
        cls.emp_b = make_employee(
            company=cls.company_b,
            email="b@emp.test",
            first_name="Bob",
            last_name="B",
        )

    def test_company_a_sees_only_own_employees(self):
        self.set_company_context(self.company_a.pk)
        visible = list(Employee.objects.all())
        self.assertIn(self.emp_a, visible)
        self.assertNotIn(self.emp_b, visible)

    def test_company_b_sees_only_own_employees(self):
        self.set_company_context(self.company_b.pk)
        visible = list(Employee.objects.all())
        self.assertNotIn(self.emp_a, visible)
        self.assertIn(self.emp_b, visible)

    def test_entire_bypasses_company_filter(self):
        self.set_company_context(self.company_a.pk)
        all_rows = list(Employee.objects.entire())
        self.assertIn(self.emp_a, all_rows)
        self.assertIn(self.emp_b, all_rows)

    def test_work_info_company_links(self):
        self.assertEqual(
            EmployeeWorkInformation.objects.get(employee_id=self.emp_a).company_id_id,
            self.company_a.pk,
        )
        self.assertEqual(
            EmployeeWorkInformation.objects.get(employee_id=self.emp_b).company_id_id,
            self.company_b.pk,
        )

    def test_employee_import_requires_add_permission(self):
        user = make_user("company_a_import_viewer")
        make_employee(
            company=self.company_a,
            email="company-a-import-viewer@example.test",
            user=user,
        )
        view_permission = Permission.objects.get(
            content_type__app_label="employee",
            content_type__model="employee",
            codename="view_employee",
        )
        user.user_permissions.add(view_permission)
        self.client.force_login(user)
        session = self.client.session
        session["selected_company"] = str(self.company_a.pk)
        session.save()

        list_response = self.client.get(
            reverse("employees-list"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(list_response.status_code, 200)
        response = self.client.post(
            "/" + list_response.context["post_import_sheet_path"],
            {},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 403)

    def test_employee_import_requires_change_permission_for_references(self):
        user = make_user("company_a_import_add_only")
        make_employee(
            company=self.company_a,
            email="company-a-import-add-only@example.test",
            user=user,
        )
        permissions = Permission.objects.filter(
            content_type__app_label="employee",
            content_type__model="employee",
            codename__in={"view_employee", "add_employee"},
        )
        user.user_permissions.add(*permissions)
        self.client.force_login(user)
        session = self.client.session
        session["selected_company"] = str(self.company_a.pk)
        session.save()

        list_response = self.client.get(
            reverse("employees-list"), HTTP_HX_REQUEST="true"
        )
        view = EmployeesList()
        reference_header = (
            get_verbose_name_from_field_path(
                view.model,
                view.update_reference,
                view.import_related_model_column_mapping,
            )
            + " | Reference"
        )
        workbook = Workbook()
        sheet = workbook.active
        sheet.append([reference_header])
        sheet.append([self.emp_a.pk])
        contents = io.BytesIO()
        workbook.save(contents)
        upload = SimpleUploadedFile(
            "employees.xlsx",
            contents.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response = self.client.post(
            "/" + list_response.context["post_import_sheet_path"],
            {"file": upload},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 403)

    def test_employee_import_cannot_assign_an_unavailable_company(self):
        user = make_user("company_a_importer")
        make_employee(
            company=self.company_a,
            email="company-a-importer@example.test",
            user=user,
        )
        permissions = Permission.objects.filter(
            content_type__app_label="employee",
            content_type__model="employee",
            codename__in={"view_employee", "add_employee", "change_employee"},
        )
        user.user_permissions.add(*permissions)
        self.client.force_login(user)
        session = self.client.session
        session["selected_company"] = str(self.company_a.pk)
        session.save()

        list_response = self.client.get(
            reverse("employees-list"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(list_response.status_code, 200)

        view = EmployeesList()
        mappings = view.import_related_model_column_mapping
        headers = {
            field: get_verbose_name_from_field_path(view.model, field, mappings)
            for field in view.import_fields
        }
        reference_header = (
            get_verbose_name_from_field_path(view.model, view.update_reference, mappings)
            + " | Reference"
        )
        fields = [
            "employee_first_name",
            "employee_last_name",
            "email",
            "phone",
            "employee_work_info__company_id",
            "employee_work_info__location",
        ]
        workbook = Workbook()
        sheet = workbook.active
        sheet.append([reference_header, *(headers[field] for field in fields)])
        sheet.append(
            [
                None,
                "Cross Tenant",
                "Import",
                "cross-tenant-import@example.test",
                "9112223333",
                self.company_b.company,
                "Cross Tenant Site",
            ]
        )
        sheet.append(
            [
                None,
                "Same Tenant",
                "Import",
                "same-tenant-import@example.test",
                "9112223344",
                self.company_a.company,
                "Company A Site",
            ]
        )
        original_location = EmployeeWorkInformation.objects.unscoped().get(
            employee_id=self.emp_a
        ).location
        sheet.append(
            [
                self.emp_a.pk,
                self.emp_a.employee_first_name,
                self.emp_a.employee_last_name,
                self.emp_a.email,
                self.emp_a.phone,
                self.company_b.company,
                "Unauthorized location update",
            ]
        )
        contents = io.BytesIO()
        workbook.save(contents)
        upload = SimpleUploadedFile(
            "employees.xlsx",
            contents.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

        response = self.client.post(
            "/" + list_response.context["post_import_sheet_path"],
            {"file": upload},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        imported = Employee.objects.unscoped().filter(
            email="cross-tenant-import@example.test"
        )
        self.assertFalse(imported.exists())
        same_tenant_import = Employee.objects.unscoped().get(
            email="same-tenant-import@example.test"
        )
        self.assertEqual(
            same_tenant_import.employee_work_info.company_id_id, self.company_a.pk
        )
        existing_work_info = EmployeeWorkInformation.objects.unscoped().get(
            employee_id=self.emp_a
        )
        self.assertEqual(existing_work_info.company_id_id, self.company_a.pk)
        self.assertEqual(existing_work_info.location, original_location)
