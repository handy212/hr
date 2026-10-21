from django.test import TestCase, override_settings
from django.utils import timezone
from horilla.horilla_middlewares import current_company_id
from horilla.testkit import make_company, make_employee
from horilla.testkit.company import CompanyFilterTestMixin


@override_settings(COMPANY_SCOPING_STRICT=True)
class DomainIsolationTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.a=make_company('Domain A');self.b=make_company('Domain B')
        self.ea=make_employee(company=self.a,email='domain-a@test.example')
        self.eb=make_employee(company=self.b,email='domain-b@test.example')

    def test_employee_google_credentials_are_not_cross_tenant_secrets(self):
        from horilla_meet.models import GoogleCredential
        own=GoogleCredential.objects.unscoped().create(employee_id=self.ea,expires_at=timezone.now())
        GoogleCredential.objects.unscoped().create(employee_id=self.eb,expires_at=timezone.now())
        self.set_company_context(self.a.pk)
        self.assertEqual(list(GoogleCredential.objects.values_list('pk',flat=True)),[own.pk])
        self.clear_company_context()
        self.assertEqual(GoogleCredential.objects.count(),0)

    def test_offboarding_tasks_assignments_and_history_stay_in_company(self):
        from offboarding.models import Offboarding, OffboardingStage, OffboardingEmployee, OffboardingTask, EmployeeTask
        records=[]
        for company,employee in [(self.a,self.ea),(self.b,self.eb)]:
            process=Offboarding.objects.unscoped().create(title=company.company,description='Exit',company_id=company)
            stage=OffboardingStage.objects.unscoped().create(title='Owned stage',type='notice_period',offboarding_id=process)
            off_employee=OffboardingEmployee.objects.unscoped().create(employee_id=employee,stage_id=stage)
            task=OffboardingTask.objects.unscoped().create(title='Return assets',stage_id=stage)
            assignment=EmployeeTask.objects.unscoped().create(employee_id=off_employee,task_id=task)
            records.append((task,assignment))
        self.set_company_context(self.a.pk)
        self.assertEqual(list(OffboardingTask.objects.values_list('pk',flat=True)),[records[0][0].pk])
        self.assertEqual(list(EmployeeTask.objects.values_list('pk',flat=True)),[records[0][1].pk])
        self.assertTrue(records[0][1].history.exists())
        self.assertFalse(records[1][1].history.exists())

    def test_biometric_scheduler_only_runs_in_active_device_company_and_cleans_context(self):
        from biometric.models import BiometricDevices
        from saas.device_access import tenant_biometric_scheduler
        from saas.models import Tenant
        device=BiometricDevices.objects.unscoped().create(name='Scoped device',machine_type='zk',company_id=self.a)
        observed=[]
        @tenant_biometric_scheduler
        def job(device_id):
            from horilla.horilla_middlewares import get_selected_company
            observed.append(get_selected_company())
        token=current_company_id.set(str(self.b.pk))
        try:
            job(device.pk)
            self.assertEqual(observed,[str(self.a.pk)])
            from horilla.horilla_middlewares import get_selected_company
            self.assertEqual(get_selected_company(),str(self.b.pk))
            Tenant.objects.create(company=self.a,slug='blocked-biometric',status='suspended')
            job(device.pk)
            self.assertEqual(len(observed),1)
        finally:
            current_company_id.reset(token)
