"""Device-to-HR boundary: explicit ownership, lifecycle and bounded context."""
from functools import wraps
from horilla.horilla_middlewares import current_company_id
from saas.access import scope_is_suspended


def tenant_device_operation(func):
    @wraps(func)
    def guarded(device, *args, **kwargs):
        if not device.company_id_id or scope_is_suspended([device.company_id_id]):
            return 'tenant_blocked'
        token = current_company_id.set(str(device.company_id_id))
        try:
            return func(device, *args, **kwargs)
        finally:
            current_company_id.reset(token)
    return guarded


def tenant_biometric_scheduler(func):
    @wraps(func)
    def guarded(device_id, *args, **kwargs):
        from biometric.models import BiometricDevices
        device = BiometricDevices.objects.unscoped().filter(pk=device_id).first()
        if not device or not device.company_id_id or scope_is_suspended([device.company_id_id]):
            return None
        token = current_company_id.set(str(device.company_id_id))
        try:
            return func(device_id, *args, **kwargs)
        finally:
            current_company_id.reset(token)
    return guarded
