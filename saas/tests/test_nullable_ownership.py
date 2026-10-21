from django.test import TestCase, override_settings
from horilla.testkit import make_company
from horilla.testkit.company import CompanyFilterTestMixin
from employee.models import Employee, Policy, PolicyMultipleFile


@override_settings(COMPANY_SCOPING_STRICT=True)
class NullableOwnershipTests(CompanyFilterTestMixin, TestCase):
    def test_unassigned_employee_is_not_a_shared_employee(self):
        company=make_company('Private ownership')
        orphan=Employee.objects.unscoped().create(employee_first_name='Unassigned',email='orphan@test.example',phone='9999999')
        self.set_company_context(company.pk)
        self.assertFalse(Employee.objects.filter(pk=orphan.pk).exists())
        self.assertTrue(Employee.objects.unscoped().filter(pk=orphan.pk).exists())

    def test_policy_reverse_accessor_is_resolved_and_orphan_files_are_private(self):
        a=make_company('Policy A');b=make_company('Policy B')
        own=PolicyMultipleFile.objects.unscoped().create(attachment='a.txt')
        other=PolicyMultipleFile.objects.unscoped().create(attachment='b.txt')
        orphan=PolicyMultipleFile.objects.unscoped().create(attachment='orphan.txt')
        for company,file in [(a,own),(b,other)]:
            policy=Policy.objects.unscoped().create(title=company.company,body='Test')
            policy.company_id.add(company);policy.attachments.add(file)
        self.set_company_context(a.pk)
        self.assertEqual(list(PolicyMultipleFile.objects.values_list('pk',flat=True)),[own.pk])
        self.assertFalse(PolicyMultipleFile.objects.filter(pk=orphan.pk).exists())
