from types import SimpleNamespace

from django.core.cache import cache
from django.test import TestCase, override_settings

from base.models import Department
from horilla.horilla_middlewares import _thread_locals
from horilla.testkit import make_company, make_user
from horilla.testkit.company import CompanyFilterTestMixin
from report.filter_schema import build_filter_options


@override_settings(COMPANY_SCOPING_STRICT=True)
class ReportCacheIsolationTests(CompanyFilterTestMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.a = make_company('Report A')
        self.b = make_company('Report B')
        self.da = Department.objects.unscoped().create(department='A secret')
        self.da.company_id.add(self.a)
        self.db = Department.objects.unscoped().create(department='B secret')
        self.db.company_id.add(self.b)
        self.definition = SimpleNamespace(slug='tenant-test', filter_fields=('department_id','company_id'))
        self.previous = getattr(_thread_locals,'request',None)

    def tearDown(self):
        _thread_locals.request = self.previous
        cache.clear()
        super().tearDown()

    def test_single_company_cache_does_not_leak_and_company_options_are_bounded(self):
        self.set_company_context(self.a.pk)
        a = build_filter_options(self.definition)
        self.set_company_context(self.b.pk)
        b = build_filter_options(self.definition)
        self.assertEqual([x['id'] for x in a['departments']], [self.da.pk])
        self.assertEqual([x['id'] for x in b['departments']], [self.db.pk])
        self.assertEqual([x['id'] for x in a['companies']], [self.a.pk])
        self.assertEqual([x['id'] for x in b['companies']], [self.b.pk])

    def test_all_company_cache_is_bounded_by_effective_assignments(self):
        self.set_company_context('all')
        _thread_locals.request = SimpleNamespace(all_my_company_ids={self.a.pk}, user=make_user('report_a'))
        a = build_filter_options(self.definition)
        _thread_locals.request = SimpleNamespace(all_my_company_ids={self.b.pk}, user=make_user('report_b'))
        b = build_filter_options(self.definition)
        self.assertEqual([x['id'] for x in a['departments']], [self.da.pk])
        self.assertEqual([x['id'] for x in b['departments']], [self.db.pk])
        self.assertEqual([x['id'] for x in b['companies']], [self.b.pk])

    def test_missing_context_exposes_no_company_directory(self):
        self.clear_company_context()
        options = build_filter_options(self.definition)
        self.assertEqual(options['companies'], [])
        self.assertEqual(options['departments'], [])
