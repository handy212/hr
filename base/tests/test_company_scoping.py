"""
Regression tests for company scoping (SaaS isolation Phase 0).

Covers:
- HorillaCompanyManager fail-closed behavior when COMPANY_SCOPING_STRICT=True.
- The scoping audit runs and reports coverage.
- CI gate: no NEW unscoped model may be added without updating the
  KNOWN_UNSCOPED allowlist (shrink it as coverage is completed).
"""

from django.test import TestCase, override_settings

from base.company_scoping import audit_model_scoping
from base.models import Company, Department
from horilla.testkit.company import CompanyFilterTestMixin


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
    "base.companygroupassignment",
    "base.companylanguagesetting",
    "base.dashboardemployeecharts",
    "base.defaultexportpermission",
    "base.driverviewed",
    "base.dynamicemailconfiguration",
    "base.historicalrotatingshiftassign",
    "base.historicalrotatingworktypeassign",
    "base.historicalshiftrequest",
    "base.historicalworktyperequest",
    "base.integrationapps",
    "base.notificationsound",
    "base.penaltyaccounts",
    "base.rosterpublishlog",
    "base.setupchecklistdismissal",
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
    "facedetection.facedetection",
    "geofencing.geofencing",
    "helpdesk.attachment",
    "helpdesk.claimrequest",
    "helpdesk.comment",
    "helpdesk.historicalticket",
    "horilla_audit.accountblockunblock",
    "horilla_audit.auditmodelconfig",
    "horilla_audit.audittag",
    "horilla_audit.historytrackingfields",
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
    "horilla_theme.companytheme",
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
