"""
Regression tests for company scoping (SaaS isolation Phase 0).

Covers:
- HorillaCompanyManager fail-closed behavior when COMPANY_SCOPING_STRICT=True.
- The scoping audit runs and reports coverage.
- CI gate: no NEW unscoped model may be added without updating the
  KNOWN_UNSCOPED allowlist (shrink it as coverage is completed).
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from base.company_scoping import audit_model_scoping
from base.models import Company, Department, DynamicEmailConfiguration
from horilla.testkit import make_employee
from horilla.testkit.company import CompanyFilterTestMixin
from horilla_auth.models import HorillaUser


class StrictScopingTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.company = Company.objects.create(company="Strict Co")
        self.department = Department.objects.create(department="Engineering")
        self.department.company_id.add(self.company)

    def test_no_context_returns_unscoped_when_strict_disabled(self):
        self.clear_company_context()
        with override_settings(COMPANY_SCOPING_STRICT=False):
            self.assertGreaterEqual(Department.objects.count(), 1)

    def test_no_context_returns_empty_when_strict_enabled(self):
        self.clear_company_context()
        with override_settings(COMPANY_SCOPING_STRICT=True):
            self.assertEqual(Department.objects.count(), 0)

    def test_unscoped_escape_hatch_bypasses_strict_mode(self):
        self.clear_company_context()
        with override_settings(COMPANY_SCOPING_STRICT=True):
            self.assertGreaterEqual(Department.objects.unscoped().count(), 1)
            self.assertGreaterEqual(Department.objects.entire().count(), 1)

    def test_company_context_still_filters_when_strict(self):
        other = Company.objects.create(company="Other Co")
        sales = Department.objects.create(department="Sales")
        sales.company_id.add(other)
        self.set_company_context(self.company.id)
        with override_settings(COMPANY_SCOPING_STRICT=True):
            names = set(Department.objects.values_list("department", flat=True))
        self.assertIn("Engineering", names)
        self.assertNotIn("Sales", names)


class ScopingAuditTests(TestCase):
    def test_audit_runs_and_reports(self):
        report = audit_model_scoping()
        self.assertGreater(report.total, 0)
        self.assertGreater(len(report.scoped), 0)
        labels = {e.label for e in report.scoped}
        self.assertIn("base.department", labels)

    def test_no_new_unscoped_models(self):
        """
        CI gate: every model not in the allowlist below must be scoped.
        When adding a model, either scope it with HorillaCompanyManager,
        add it to GLOBAL_MODELS in base/company_scoping.py if it is truly
        platform-level, or (temporarily) extend this allowlist with a TODO.
        """
        report = audit_model_scoping()
        current = {e.label for e in report.unscoped_gaps}
        new_gaps = current - KNOWN_UNSCOPED
        self.assertEqual(
            new_gaps,
            set(),
            "New unscoped models detected — scope them with "
            "HorillaCompanyManager or justify in KNOWN_UNSCOPED: "
            + ", ".join(sorted(new_gaps)),
        )


# Snapshot of unscoped models at Phase 0 kickoff (2026-10-14). This list must
# only shrink — scope models with HorillaCompanyManager to remove them here.
# Regenerate with: python manage.py audit_company_scoping --json
@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class StrictMailConfigTests(CompanyFilterTestMixin, TestCase):
    """
    Mail flows must keep working with COMPANY_SCOPING_STRICT=True, including
    scheduler mail that runs without any request/company context.
    """

    def setUp(self):
        self.company = Company.objects.create(company="Strict Mail Co")

    def _create_config(self, username, is_primary=False, company_id=None):
        return DynamicEmailConfiguration.objects.unscoped().create(
            username=username,
            host="smtp.example.com",
            from_email=f"{username}@example.com",
            is_primary=is_primary,
            company_id=company_id,
        )

    def test_config_lookup_without_context_finds_primary_when_strict(self):
        primary = self._create_config("primary", is_primary=True)
        self.clear_company_context()
        from base.backends import ConfiguredEmailBackend

        with override_settings(COMPANY_SCOPING_STRICT=True):
            found = ConfiguredEmailBackend.get_dynamic_email_config()

        self.assertIsNotNone(found)
        self.assertEqual(found.pk, primary.pk)

    def test_context_company_config_reachable_when_strict(self):
        self._create_config("primary", is_primary=True)
        tenant = self._create_config(
            "tenant", company_id=self.company
        )
        self.set_company_context(self.company.id)
        from base.backends import ConfiguredEmailBackend

        with override_settings(COMPANY_SCOPING_STRICT=True):
            found = ConfiguredEmailBackend.get_dynamic_email_config()

        # No request in context, so the backend looks up company_id=None and
        # falls back to the global primary; the tenant row must at least be
        # reachable through the scoped manager.
        self.assertEqual(found.username, "primary")
        with override_settings(COMPANY_SCOPING_STRICT=True):
            self.assertEqual(
                DynamicEmailConfiguration.objects.filter(
                    company_id=self.company
                ).first(),
                tenant,
            )

    def test_save_primary_bookkeeping_without_context_when_strict(self):
        first = self._create_config("first", is_primary=True)
        self.clear_company_context()

        with override_settings(COMPANY_SCOPING_STRICT=True):
            second = DynamicEmailConfiguration(
                username="second",
                host="smtp.example.com",
                is_primary=True,
            )
            second.save()

        first.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertTrue(
            DynamicEmailConfiguration.objects.unscoped().get(pk=second.pk).is_primary
        )

    def test_first_row_becomes_primary_without_context_when_strict(self):
        self.clear_company_context()
        with override_settings(COMPANY_SCOPING_STRICT=True):
            config = DynamicEmailConfiguration(
                username="only", host="smtp.example.com"
            )
            config.save()

        self.assertTrue(
            DynamicEmailConfiguration.objects.unscoped().get(pk=config.pk).is_primary
        )

    def test_replace_primary_mail_view_is_null_safe_when_strict(self):
        user = HorillaUser.objects.create_user(
            username="mailadmin", password="pass", is_superuser=True
        )
        make_employee(company=self.company, email="mailadmin@example.com", user=user)
        previous = self._create_config("previous", is_primary=True)
        config = self._create_config("candidate", company_id=self.company)
        self.client.force_login(user)
        # The company middleware scopes requests from session, not from the
        # ContextVar, so pin the session company for this request.
        session = self.client.session
        session["selected_company"] = str(self.company.id)
        session.save()

        with override_settings(COMPANY_SCOPING_STRICT=True):
            response = self.client.post(
                reverse("replace-primary-mail"), {"replace_mail": config.id}
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            DynamicEmailConfiguration.objects.unscoped().filter(
                pk=config.pk
            ).exists()
        )
        self.assertTrue(
            DynamicEmailConfiguration.objects.unscoped().get(pk=config.pk).is_primary
        )
        self.assertFalse(
            DynamicEmailConfiguration.objects.unscoped().get(pk=previous.pk).is_primary
        )

    def test_replace_primary_mail_requires_change_permission(self):
        user = HorillaUser.objects.create_user(
            username="mailviewer", password="pass"
        )
        make_employee(company=self.company, email="mailviewer@example.com", user=user)
        previous = self._create_config("previous", is_primary=True)
        candidate = self._create_config("candidate", company_id=self.company)
        self.client.force_login(user)
        session = self.client.session
        session["selected_company"] = str(self.company.id)
        session.save()

        self.client.post(
            reverse("replace-primary-mail"), {"replace_mail": candidate.id}
        )

        self.assertTrue(
            DynamicEmailConfiguration.objects.unscoped().filter(pk=previous.pk).exists()
        )
        self.assertFalse(
            DynamicEmailConfiguration.objects.unscoped().get(pk=candidate.pk).is_primary
        )


KNOWN_UNSCOPED = {
    "accessibility.defaultaccessibility",
    "asset.assetdocuments",
    "asset.assetreport",
    "asset.assetrequestcomment",
    "asset.assetservicerequestnote",
    "asset.returnimages",
    "attendance.attendancerequestcomment",
    "attendance.attendancerequestfile",
    "attendance.batchattendance",
    "attendance.historicalattendance",
    "attendance.historicalattendanceactivity",
    "auditlog.logentry",
    "axes.accessattemptexpiration",
    "base.announcementcomment",
    "base.announcementview",
    "base.attachment",
    "base.baserequestfile",
    # Kept unscoped intentionally: base.auth_backends.CompanyScopedBackend
    # resolves a user's company assignments across all companies (including
    # "all"/missing context); filtering here would break permission checks.
    "base.companygroupassignment",
    "base.dashboardemployeecharts",
    "base.driverviewed",
    "base.historicalrotatingshiftassign",
    "base.historicalrotatingworktypeassign",
    "base.historicalshiftrequest",
    "base.historicalworktyperequest",
    "base.notificationsound",
    "base.penaltyaccounts",
    "base.rosterpublishlog",
    "base.shiftrequestcomment",
    "base.worktyperequestcomment",
    "biometric.biometricemployees",
    "biometric.cosecattendancearguments",
    "device_gateway.terminalcommand",
    "device_gateway.terminalemployee",
    "device_gateway.terminalpunchlog",
    "device_gateway.terminalsnapshot",
    "device_gateway.terminalsynctask",
    "device_gateway.terminalsystemrecord",
    "employee.actiontype",
    "employee.employeetag",
    "employee.historicalbonuspoint",
    "employee.historicalemployeeworkinformation",
    "employee.notefiles",
    "employee.policymultiplefile",
    "employee.profileeditfeature",
    "facedetection.employeefacedetection",
    "helpdesk.attachment",
    "helpdesk.claimrequest",
    "helpdesk.comment",
    "helpdesk.historicalticket",
    "horilla_audit.accountblockunblock",
    "horilla_audit.auditmodelconfig",
    "horilla_audit.audittag",
    "horilla_auth.authusergroups",
    "horilla_auth.authuseruserpermissions",
    "horilla_auth.legacyuser",
    "horilla_automations.mailautomation",
    "horilla_backup.googledrivebackup",
    "horilla_backup.localbackup",
    "horilla_dbtemplate.template",
    "horilla_dbtemplate.templateversion",
    "horilla_ldap.ldapsettings",
    "horilla_meet.googlecredential",
    "horilla_meet.googlemeeting",
    "horilla_meet.interviewmeetinglink",
    "horilla_meet.pmsmeetinglink",
    "horilla_theme.horillacolortheme",
    "horilla_views.activegroup",
    "horilla_views.activetab",
    "horilla_views.activeview",
    "horilla_views.columnorder",
    "horilla_views.savedfilter",
    "horilla_views.togglecolumn",
    "leave.compensatoryleaverequestcomment",
    "leave.historicalavailableleave",
    "leave.historicalcompensatoryleaverequest",
    "leave.historicalleaveallocationrequest",
    "leave.historicalleaverequest",
    "leave.leaveallocationrequestcomment",
    "leave.leaverequestcomment",
    "leave.leaverequestconditionapproval",
    "leave.leaverequestfile",
    "leave.leavetypecondition",
    "notifications.notification",
    "offboarding.employeetask",
    "offboarding.exitreason",
    "offboarding.historicalemployeetask",
    "offboarding.historicalresignationletter",
    "offboarding.offboardingnote",
    "offboarding.offboardingstage",
    "offboarding.offboardingstagemultiplefile",
    "offboarding.offboardingtask",
    "onboarding.historicalcandidatetask",
    "payroll.encashmentgeneralsettings",
    "payroll.historicalcontract",
    "payroll.historicalpayslip",
    "payroll.multiplecondition",
    "payroll.reimbursementfile",
    "payroll.reimbursementmultipleattachment",
    "payroll.reimbursementrequestcomment",
    "payroll.taxbracket",
    "pms.anonymousfeedback",
    "pms.historicalcomment",
    "pms.historicalemployeekeyresult",
    "pms.historicalemployeeobjective",
    # Historical tables deferred to Phase 1 — scoped via parent model
    # (written by simple-history middleware without request company context).
    "pms.historicalkeyresult",
    "pms.historicalobjective",
    "recruitment.candidatedocument",
    "recruitment.candidaterating",
    "recruitment.historicalcandidate",
    "recruitment.historicalrejectedcandidate",
    "recruitment.historicalstage",
    "recruitment.questionordering",
    "recruitment.resume",
    "recruitment.skill",
    "recruitment.stagefiles",
    "token_blacklist.blacklistedtoken",
    "token_blacklist.outstandingtoken",
    "whatsapp.whatsappflowdetails",
}
