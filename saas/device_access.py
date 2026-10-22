"""Device-to-HR boundary: explicit ownership, lifecycle and bounded context."""

from functools import wraps
from horilla.horilla_middlewares import current_company_id
from saas.features import company_has_feature


def tenant_device_operation(func):
    @wraps(func)
    def guarded(device, *args, **kwargs):
        if not device.company_id_id or not company_has_feature(
            device.company_id_id, "devices"
        ):
            return "tenant_blocked"
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
        if (
            not device
            or not device.company_id_id
            or not company_has_feature(device.company_id_id, "devices")
        ):
            return None
        token = current_company_id.set(str(device.company_id_id))
        try:
            return func(device_id, *args, **kwargs)
        finally:
            current_company_id.reset(token)

    return guarded
