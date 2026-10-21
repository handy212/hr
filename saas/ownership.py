"""Ownership and write protection for platform extensions to upstream models."""
from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_save, pre_save, pre_delete
from horilla.horilla_middlewares import _thread_locals, get_selected_company

RESOURCE_FIELDS = {
    'employee.employeetag': 'employee_tag',
    'employee.actiontype': 'action_type',
    'recruitment.skill': 'skill',
    'pms.anonymousfeedback': 'anonymous_feedback',
    'payroll.encashmentgeneralsettings': 'encashment_settings',
    'horilla_automations.mailautomation': 'mail_automation',
}
PLATFORM_MODELS = frozenset({'employee.profileeditfeature','horilla_dbtemplate.template','horilla_dbtemplate.templateversion'})


def protect_resource(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    label = sender._meta.label_lower
    if label not in RESOURCE_FIELDS and label not in PLATFORM_MODELS:
        return
    request = getattr(_thread_locals, 'request', None)
    if request is None:
        return  # Trusted offline tasks; no ownership is inferred without scope.
    user = request.user
    if user.is_authenticated and user.is_active and user.is_superuser:
        return
    if label in PLATFORM_MODELS or not user.is_authenticated:
        raise PermissionDenied('Platform access is required to change this resource.')
    selected = get_selected_company()
    if not selected or selected == 'all':
        raise PermissionDenied('Select one company before changing this resource.')
    if instance.pk:
        from saas.models import ResourceOwnership
        owners = set(ResourceOwnership.objects.filter(**{RESOURCE_FIELDS[label]+'_id': instance.pk}).values_list('company_id', flat=True))
        if owners != {int(selected)}:
            raise PermissionDenied('Shared or unassigned resources require platform administration.')


def bind_new_resource(sender, instance, created, **kwargs):
    if kwargs.get("raw"):
        return
    label = sender._meta.label_lower
    if not created or label not in RESOURCE_FIELDS:
        return
    selected = get_selected_company()
    if selected and selected != 'all':
        from saas.models import ResourceOwnership
        ResourceOwnership.objects.get_or_create(company_id=selected, **{RESOURCE_FIELDS[label]: instance})


def assign_resource(*, resource, company, actor):
    from saas.models import ResourceOwnership
    from saas.services import require_platform_operator
    require_platform_operator(actor)
    field = RESOURCE_FIELDS[resource._meta.label_lower]
    owner, _ = ResourceOwnership.objects.get_or_create(company=company, **{field: resource})
    import logging
    logging.getLogger(__name__).info('Resource ownership assigned actor_id=%s company_id=%s resource=%s resource_id=%s',actor.pk,company.pk,resource._meta.label_lower,resource.pk)
    return owner


def register_ownership_signals():
    pre_save.connect(protect_resource, dispatch_uid='saas.protect_resource.save', weak=False)
    pre_delete.connect(protect_resource, dispatch_uid='saas.protect_resource.delete', weak=False)
    post_save.connect(bind_new_resource, dispatch_uid='saas.bind_resource', weak=False)
