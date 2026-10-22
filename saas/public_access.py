"""Published recruitment identity establishes scope for anonymous applications."""
from functools import wraps
import json

from django.http import HttpResponseNotFound
from horilla.horilla_middlewares import current_company_id
from saas.access import blocked_company_ids


def public_recruitment_scope(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        recruitment_id = kwargs.get('id') or request.GET.get('recruitmentId')
        if not recruitment_id and request.session.get('candidate'):
            try:
                data = json.loads(request.session['candidate'])
                recruitment_id = data[0]['fields']['recruitment_id']
            except (ValueError, KeyError, IndexError, TypeError):
                return HttpResponseNotFound('Recruitment not found.')
        if not recruitment_id:
            return view(request, *args, **kwargs)
        from recruitment.models import Recruitment
        try:
            recruitment = Recruitment.default.filter(
                pk=recruitment_id, is_published=True, is_active=True, closed=False,
                company_id__isnull=False,
            ).exclude(company_id__in=blocked_company_ids(feature='recruitment')).first()
        except (ValueError, TypeError, OverflowError):
            recruitment = None
        if recruitment is None:
            return HttpResponseNotFound('Recruitment not found.')
        token = current_company_id.set(str(recruitment.company_id_id))
        try:
            return view(request, *args, **kwargs)
        finally:
            current_company_id.reset(token)
    return guarded
