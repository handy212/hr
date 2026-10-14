"""
Report HorillaCompanyManager coverage across all installed models.

Usage:
    python manage.py audit_company_scoping            # summary + gaps
    python manage.py audit_company_scoping --full     # include scoped + global
    python manage.py audit_company_scoping --json     # machine-readable
"""

import json

from django.core.management.base import BaseCommand

from base.company_scoping import audit_model_scoping


class Command(BaseCommand):
    help = "Audit company-scoping coverage of all models (SaaS isolation audit)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--full",
            action="store_true",
            help="Also list scoped and intentionally-global models.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Output the full report as JSON.",
        )

    def handle(self, *args, **options):
        report = audit_model_scoping()

        if options["as_json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "scoped": [e.label for e in report.scoped],
                        "unscoped_gaps": [
                            {
                                "model": e.label,
                                "has_company_fk": e.has_company_fk,
                            }
                            for e in report.unscoped_gaps
                        ],
                        "global": [e.label for e in report.global_models],
                    },
                    indent=2,
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Scoped models: {len(report.scoped)}/{report.total}"
            )
        )
        if options["full"]:
            for e in sorted(report.scoped, key=lambda e: e.label):
                self.stdout.write(f"  [scoped] {e.label} via {e.company_filter_path}")

        self.stdout.write(
            self.style.WARNING(
                f"Unscoped models (potential cross-tenant gaps): "
                f"{len(report.unscoped_gaps)}"
            )
        )
        for e in sorted(report.unscoped_gaps, key=lambda e: e.label):
            marker = " [has company FK — definite gap]" if e.has_company_fk else ""
            self.stdout.write(f"  [unscoped] {e.label}{marker}")

        if options["full"]:
            self.stdout.write(f"Intentionally global: {len(report.global_models)}")
            for e in sorted(report.global_models, key=lambda e: e.label):
                self.stdout.write(f"  [global] {e.label}")
