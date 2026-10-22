"""Scoped managers for resources owned through alternative parent relations."""

from functools import reduce
from operator import or_

from django.db.models import Q
from base.horilla_company_manager import HorillaCompanyManager
from saas.scope import effective_company_ids


class MultiParentCompanyManager(HorillaCompanyManager):
    def __init__(self, *paths):
        if not paths:
            raise ValueError("At least one ownership path is required.")
        super().__init__(paths[0])
        self.owner_paths = paths

    def get_queryset(self):
        from saas.access import active_company_ids

        queryset = self.unscoped()
        ids = effective_company_ids()
        if not ids:
            return queryset.none()
        from saas.features import feature_for_model

        ids = active_company_ids(ids, feature=feature_for_model(self.model))
        allowed = reduce(or_, (Q(**{f"{path}__in": ids}) for path in self.owner_paths))
        # An orphan has no owner and therefore never matches any allowed path.
        return queryset.filter(allowed).distinct()
