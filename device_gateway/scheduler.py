"""
Offline monitor: flag terminals whose heartbeat has gone silent.

Registered in device_gateway.apps.ready(); executed by the single
``manage.py run_scheduler`` process (see horilla/scheduling.py).
"""

from datetime import timedelta

from django.utils import timezone

from horilla.scheduling import register_job

from .models import TerminalDevice

# A terminal is offline after missing 3 heartbeat cycles. The spec default
# keepalive is 15-30s; 3 minutes is a safe bound for either.
OFFLINE_AFTER = timedelta(minutes=3)


def mark_offline_devices():
    cutoff = timezone.now() - OFFLINE_AFTER
    TerminalDevice.objects.filter(is_online=True, last_seen__lt=cutoff).update(
        is_online=False
    )


register_job(mark_offline_devices, "interval", job_id="device_gateway.offline", minutes=1)
