"""Atomic provisioning; all cross-company operations require a platform actor."""

import logging

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.views.decorators.debug import sensitive_variables

from base.models import Company, CompanyGroupAssignment
from saas.models import Tenant
from saas.forms import TenantProvisionForm
from employee.models import Employee, EmployeeWorkInformation
from horilla.horilla_middlewares import current_company_id
from horilla_auth.models import HorillaUser

logger = logging.getLogger(__name__)


def require_platform_operator(user):
    if not (user and user.is_authenticated and user.is_active and user.is_superuser):
        raise PermissionError("Platform operator access is required.")


@sensitive_variables("data", "values", "form")
def provision_tenant(*, data, actor):
    require_platform_operator(actor)
    if not (settings.COMPANY_SCOPING_STRICT and settings.COMPANY_SCOPED_PERMISSIONS):
        raise ValidationError(
            "Enable strict company scoping and company-scoped permissions before provisioning tenants."
        )
    form = TenantProvisionForm(data)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    values = form.cleaned_data
    with transaction.atomic():
        company = Company(
            company=values["company_name"],
            **{
                key: values[key]
                for key in ("address", "country", "state", "city", "zip")
            },
        )
        company.full_clean()
        company.save()
        token = current_company_id.set(company.pk)
        try:
            user = HorillaUser.objects.create_user(
                username=values["admin_email"],
                email=values["admin_email"],
                first_name=values["admin_first_name"],
                last_name=values["admin_last_name"],
                password=values["admin_password"],
                is_new_employee=True,
            )
            employee = Employee.objects.create(
                employee_user_id=user,
                email=user.email,
                employee_first_name=user.first_name,
                employee_last_name=user.last_name,
                phone=values["admin_phone"],
            )
            work_info, _created = (
                EmployeeWorkInformation.objects.unscoped().update_or_create(
                    employee_id=employee, defaults={"company_id": company}
                )
            )
            employee.employee_work_info = work_info
            group = Group.objects.create(name=f"Tenant {company.pk} HR administrator")
            base_models = (
                "department",
                "jobposition",
                "jobrole",
                "worktype",
                "employeeshift",
                "employeetype",
            )
            permissions = Permission.objects.filter(
                Q(
                    content_type__app_label="base",
                    codename__in=[
                        f"{action}_{model}"
                        for model in base_models
                        for action in ("view", "add", "change")
                    ],
                )
                | Q(
                    content_type__app_label="employee",
                    codename__in=[
                        "view_employee",
                        "add_employee",
                        "change_employee",
                        "view_employeeworkinformation",
                        "change_employeeworkinformation",
                        "view_ownprofile",
                        "change_ownprofile",
                    ],
                )
            )
            group.permissions.set(permissions)
            CompanyGroupAssignment.objects.create(
                user=user, company=company, group=group
            )
            CompanyGroupAssignment.sync_user_group_membership(user, group)
            tenant = Tenant(
                company=company,
                slug=values["slug"],
                status=values["status"],
                primary_admin=user,
                created_by=actor,
            )
            tenant.full_clean()
            tenant.save()
        finally:
            current_company_id.reset(token)
    logger.info(
        "Tenant provisioned tenant_id=%s company_id=%s actor_id=%s",
        tenant.pk,
        company.pk,
        actor.pk,
    )
    return tenant
