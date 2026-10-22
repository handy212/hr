from contextlib import contextmanager

from horilla.horilla_middlewares import current_company_id


@contextmanager
def device_context(device):
    """Scope ORM access to the device's company, as the HTTP views do."""
    token = current_company_id.set(str(device.company_id_id))
    try:
        yield
    finally:
        current_company_id.reset(token)
