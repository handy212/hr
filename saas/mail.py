"""Mail ownership: company configuration first, platform-owned fallback only."""
from horilla.horilla_middlewares import get_selected_company


def resolve_mail_configuration():
    from base.models import DynamicEmailConfiguration
    selected = get_selected_company()
    configs = DynamicEmailConfiguration.objects.unscoped()
    if selected and selected != 'all':
        config = configs.filter(company_id_id=selected).first()
        if config:
            return config
    return configs.filter(company_id__isnull=True, is_primary=True).first()


def validate_mail_ownership(config):
    from django.core.exceptions import ValidationError
    if config.company_id_id and config.is_primary:
        raise ValidationError({'is_primary': 'Only a platform-owned mail server can be globally primary.'})
