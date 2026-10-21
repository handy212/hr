from django.apps import apps
from django.test import TestCase, override_settings
from employee.models import EmployeeWorkInformation
from horilla.testkit import make_company, make_employee
from horilla.testkit.company import CompanyFilterTestMixin


@override_settings(COMPANY_SCOPING_STRICT=True)
class HistoryIsolationTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        self.a=make_company('History A');self.b=make_company('History B')
        self.ea=make_employee(company=self.a,email='history-a@test.example')
        self.eb=make_employee(company=self.b,email='history-b@test.example')
        self.wa=EmployeeWorkInformation.objects.unscoped().get(employee_id=self.ea)
        self.wb=EmployeeWorkInformation.objects.unscoped().get(employee_id=self.eb)
        # Fixture updates use SQL; explicitly save snapshots for both owners.
        self.wa.save();self.wb.save()

    def test_direct_historical_queries_are_company_scoped(self):
        model=apps.get_model('employee','HistoricalEmployeeWorkInformation')
        self.set_company_context(self.a.pk)
        ids=set(model.objects.values_list('employee_id',flat=True))
        self.assertIn(self.ea.pk,ids)
        self.assertNotIn(self.eb.pk,ids)

    def test_history_descriptor_and_most_recent_cannot_cross_company(self):
        self.set_company_context(self.a.pk)
        self.assertTrue(self.wa.history.exists())
        self.assertFalse(self.wb.history.exists())
        with self.assertRaises(EmployeeWorkInformation.DoesNotExist):
            self.wb.history.most_recent()

    def test_missing_context_is_empty_but_explicit_recovery_remains_available(self):
        model=apps.get_model('employee','HistoricalEmployeeWorkInformation')
        self.clear_company_context()
        self.assertEqual(model.objects.count(),0)
        self.assertGreater(model.objects.unscoped().count(),0)
