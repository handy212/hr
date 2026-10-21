from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from base.models import DynamicEmailConfiguration
from horilla.testkit import make_company, make_employee, make_user
from horilla.testkit.company import CompanyFilterTestMixin
from saas.mail import resolve_mail_configuration


@override_settings(COMPANY_SCOPING_STRICT=True)
class MailOwnershipTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.a = make_company('Mail A')
        self.b = make_company('Mail B')

    def config(self, name, company=None, primary=False):
        return DynamicEmailConfiguration.objects.unscoped().create(
            host=name+'.example',username=name, company_id=company,is_primary=primary
        )

    def test_first_tenant_server_is_never_platform_primary(self):
        config=self.config('tenant', self.a)
        self.assertFalse(config.is_primary)
        self.clear_company_context()
        self.assertIsNone(resolve_mail_configuration())

    def test_company_primary_is_rejected_without_demoting_platform(self):
        platform=self.config('platform',primary=True)
        with self.assertRaises(ValidationError):
            self.config('tenant',self.a,primary=True)
        platform.refresh_from_db()
        self.assertTrue(platform.is_primary)

    def test_selection_uses_effective_job_scope_and_safe_platform_fallback(self):
        platform=self.config('platform',primary=True)
        a=self.config('tenant',self.a)
        self.set_company_context(self.a.pk)
        self.assertEqual(resolve_mail_configuration().pk,a.pk)
        self.set_company_context(self.b.pk)
        self.assertEqual(resolve_mail_configuration().pk,platform.pk)
        # Legacy company-primary rows must never supply another company's mail.
        DynamicEmailConfiguration.objects.unscoped().filter(pk=a.pk).update(is_primary=True)
        self.clear_company_context()
        self.assertEqual(resolve_mail_configuration().pk,platform.pk)

    def test_tenant_cannot_read_any_legacy_smtp_management_endpoint(self):
        from django.contrib.auth.models import Permission
        user=make_user('mail_tenant')
        make_employee(company=self.a,user=user,email='mail@tenant.example')
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='base',content_type__model='dynamicemailconfiguration'))
        self.client.force_login(user)
        for name,args in [('mail-server-conf',[]),('mail-server-list',[]),('create-mail-server',[]),('update-mail-server',[1]),('mail-server-create-update',[]),('mail-server-test-email',[]),('mail-server-delete',[]),('replace-primary-mail',[])]:
            response=self.client.get(reverse(name,args=args),HTTP_HX_REQUEST='true')
            self.assertEqual(response.status_code,403,name)
