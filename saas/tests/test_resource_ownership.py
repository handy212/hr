from types import SimpleNamespace
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.db import transaction
from employee.models import EmployeeTag
from horilla.horilla_middlewares import _thread_locals, current_company_id
from horilla.testkit import make_company, make_user
from horilla.testkit.company import CompanyFilterTestMixin
from saas.models import ResourceOwnership
from saas.ownership import assign_resource


@override_settings(COMPANY_SCOPING_STRICT=True)
class ResourceOwnershipTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.a=make_company('Resource A');self.b=make_company('Resource B')
        self.user=make_user('resource-user')
        self.operator=make_user('resource-operator',is_superuser=True)
        self.previous=getattr(_thread_locals,'request',None)

    def tearDown(self):
        _thread_locals.request=self.previous
        super().tearDown()

    def create_in(self, company, title):
        token=current_company_id.set(company.pk)
        try:
            return EmployeeTag.objects.create(title=title,color='red')
        finally:
            current_company_id.reset(token)

    def test_creation_binds_owner_without_adding_fields_to_upstream_model(self):
        a=self.create_in(self.a,'Private A');b=self.create_in(self.b,'Private B')
        self.set_company_context(self.a.pk)
        self.assertEqual(list(EmployeeTag.objects.values_list('pk',flat=True)),[a.pk])
        self.assertFalse(EmployeeTag.objects.filter(pk=b.pk).exists())
        self.assertEqual(ResourceOwnership.objects.get(employee_tag=a).company_id,self.a.pk)

    def test_legacy_orphan_is_quarantined_until_explicit_platform_assignment(self):
        orphan=EmployeeTag.objects.unscoped().create(title='Legacy',color='blue')
        self.set_company_context(self.a.pk)
        self.assertFalse(EmployeeTag.objects.filter(pk=orphan.pk).exists())
        with self.assertRaises(PermissionError):
            assign_resource(resource=orphan,company=self.a,actor=self.user)
        assign_resource(resource=orphan,company=self.a,actor=self.operator)
        self.assertTrue(EmployeeTag.objects.filter(pk=orphan.pk).exists())

    def test_shared_catalog_resource_cannot_be_modified_or_deleted_by_tenant(self):
        tag=self.create_in(self.a,'Shared catalog')
        assign_resource(resource=tag,company=self.b,actor=self.operator)
        self.set_company_context(self.a.pk)
        _thread_locals.request=SimpleNamespace(user=self.user)
        tag.title='Tampered'
        with self.assertRaises(PermissionDenied), transaction.atomic():tag.save()
        with self.assertRaises(PermissionDenied), transaction.atomic():tag.delete()
        self.assertEqual(EmployeeTag.objects.unscoped().get(pk=tag.pk).title,'Shared catalog')

    def test_active_owner_keeps_read_access_when_other_owner_is_suspended(self):
        from saas.models import Tenant
        tag=self.create_in(self.a,'Shared catalog')
        assign_resource(resource=tag,company=self.b,actor=self.operator)
        Tenant.objects.create(company=self.b,slug='resource-b',status='suspended')
        self.set_company_context(self.a.pk)
        self.assertTrue(EmployeeTag.objects.filter(pk=tag.pk).exists())
        self.set_company_context(self.b.pk)
        self.assertFalse(EmployeeTag.objects.filter(pk=tag.pk).exists())

    def test_tenant_cannot_change_platform_global_profile_toggle(self):
        from employee.models import ProfileEditFeature
        _thread_locals.request=SimpleNamespace(user=self.user)
        self.set_company_context(self.a.pk)
        with self.assertRaises(PermissionDenied):
            ProfileEditFeature.objects.create(is_enabled=True)
