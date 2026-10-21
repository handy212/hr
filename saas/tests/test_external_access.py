from django.test import TestCase
from django.utils import timezone

from device_gateway.models import TerminalDevice
from horilla.testkit import make_company, make_user
from report.delivery import deliver_subscription
from report.models import ReportSubscription
from saas.models import Tenant


class ExternalTenantAccessTests(TestCase):
    def setUp(self):
        self.company=make_company('Lifecycle')
        self.tenant=Tenant.objects.create(company=self.company,slug='lifecycle',status='active')

    def test_disabled_tenant_device_rejected_before_heartbeat_write(self):
        device=TerminalDevice.all_objects.create(company_id=self.company,sn='LIFECYCLE-DEVICE')
        for fields in [{'status':'suspended'},{'status':'active','deleted_at':timezone.now()}]:
            Tenant.objects.filter(pk=self.tenant.pk).update(**fields)
            response=self.client.post('/api/terminal/Device/Keepalive',{'SN':device.sn},content_type='application/json')
            self.assertEqual(response.json()['Success'],401)
            device.refresh_from_db()
            self.assertIsNone(device.last_seen)

    def test_forced_report_send_does_not_bypass_suspension_or_deletion(self):
        subscription=ReportSubscription.objects.unscoped().create(
            company_id=self.company,report_slug='any',name='Blocked report',owner=make_user('report-owner')
        )
        for fields in [{'status':'suspended'},{'status':'active','deleted_at':timezone.now()}]:
            Tenant.objects.filter(pk=self.tenant.pk).update(**fields)
            result=deliver_subscription(subscription,force=True)
            self.assertFalse(result.ok)
            self.assertEqual(result.status,'tenant_blocked')

    def test_public_recruitments_hide_disabled_company(self):
        from recruitment.models import Recruitment
        from django.urls import reverse
        rec=Recruitment.default.create(title='Private suspended recruitment',company_id=self.company,is_published=True,closed=False)
        Tenant.objects.filter(pk=self.tenant.pk).update(status='suspended')
        response=self.client.get(reverse('open-recruitments'))
        self.assertNotContains(response,rec.title)

    def test_background_scoped_queries_exclude_suspended_tenant(self):
        from employee.models import Employee
        from horilla.testkit import make_employee
        from horilla.horilla_middlewares import current_company_id
        employee=make_employee(company=self.company,email='background@tenant.example')
        token=current_company_id.set(self.company.pk)
        try:
            self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())
            Tenant.objects.filter(pk=self.tenant.pk).update(status='suspended')
            self.assertFalse(Employee.objects.filter(pk=employee.pk).exists())
            self.assertTrue(Employee.objects.unscoped().filter(pk=employee.pk).exists())
        finally:
            current_company_id.reset(token)

    def test_anonymous_application_is_scoped_to_published_recruitment(self):
        from django.urls import reverse
        from horilla.horilla_middlewares import get_selected_company
        from horilla.testkit.factories import make_recruitment
        rec=make_recruitment(company=self.company,title='Public owned recruitment')
        rec.is_published=True;rec.save(update_fields=['is_published'])
        response=self.client.get(reverse('application-form'),{'recruitmentId':rec.pk})
        self.assertEqual(response.status_code,200)
        self.assertIsNone(get_selected_company())
        Tenant.objects.filter(pk=self.tenant.pk).update(status='suspended')
        response=self.client.get(reverse('application-form'),{'recruitmentId':rec.pk})
        self.assertEqual(response.status_code,404)

    def test_device_mapping_cannot_authorize_another_company_employee(self):
        from device_gateway.models import TerminalEmployee
        from horilla.testkit import make_employee
        other=make_company('Other device owner')
        employee=make_employee(company=other,email='foreign-device@test.example')
        device=TerminalDevice.all_objects.create(company_id=self.company,sn='FOREIGN-MAPPING')
        TerminalEmployee.objects.create(device=device,employee=employee,device_user_id=5)
        response=self.client.post('/api/terminal/Device/RequestAuthorization',{
            'SN':device.sn,'RecordDetail':'{"UserID":"5","RecordID":1,"RecordType":3,"RecordDate":1}'
        })
        self.assertEqual(response.json()['Success'],0)

    def test_device_child_records_cannot_be_read_from_another_company(self):
        from device_gateway.models import TerminalCommand, TerminalSyncTask, TerminalPunchLog, TerminalSystemRecord, TerminalSnapshot
        from horilla.horilla_middlewares import current_company_id
        other=make_company('Device isolation B')
        a=TerminalDevice.all_objects.create(company_id=self.company,sn='ISOLATION-A')
        b=TerminalDevice.all_objects.create(company_id=other,sn='ISOLATION-B')
        fixtures=[
            (TerminalCommand,{'payload':{}}),
            (TerminalSyncTask,{'action':'delete_all'}),
            (TerminalPunchLog,{'record_id':2,'record_type':3,'record_date':timezone.now()}),
            (TerminalSystemRecord,{'record_id':3,'record_type':1,'record_date':timezone.now()}),
            (TerminalSnapshot,{'photo':'test.jpg'}),
        ]
        for model,fields in fixtures:
            own=model.objects.unscoped().create(device=a,**fields)
            model.objects.unscoped().create(device=b,**fields)
            token=current_company_id.set(self.company.pk)
            try:
                self.assertEqual(list(model.objects.values_list('pk',flat=True)),[own.pk],model.__name__)
            finally:
                current_company_id.reset(token)
