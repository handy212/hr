"""Definition-time history integration; no runtime model monkey-patching."""
from simple_history.manager import HistoryManager
from base.horilla_company_manager import HorillaCompanyManager


class TenantHistoryManager(HistoryManager):
    def get_super_queryset(self):
        queryset = super().get_super_queryset()
        manager = self.model.objects
        if isinstance(manager, HorillaCompanyManager):
            return queryset.filter(history_id__in=manager.values('history_id'))
        return queryset


def history_scope_fields(model, copied_fields, extra_fields):
    manager = model._default_manager
    if not isinstance(manager, HorillaCompanyManager):
        return {}
    path = manager.company_filter_path
    if not path and 'company_id' in copied_fields:
        path = 'company_id'
    if not path:
        return {}
    if path.split('__')[0] not in copied_fields:
        # M2M ownership isn't copied into history; use the explicitly configured
        # parent relation when this historical model provides one.
        if 'history_relation' not in extra_fields:
            return {}
        path = 'history_relation__' + path
    return {
        'objects': HorillaCompanyManager(path),
        '_saas_original_model_label': model._meta.label_lower,
    }
