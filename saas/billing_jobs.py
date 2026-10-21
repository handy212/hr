"""Idempotent billing notices; expiry enforcement does not depend on this job."""

from datetime import timedelta
from django.db import close_old_connections, connection
from django.utils import timezone
from saas.billing import notice
from saas.models import TenantSubscription


def notify_expiring_subscriptions():
    owns_connection = not connection.in_atomic_block
    if owns_connection:
        close_old_connections()
    try:
        now = timezone.now()
        for subscription in TenantSubscription.objects.filter(
            tenant__deleted_at__isnull=True
        ).iterator(chunk_size=250):
            until = subscription.access_until
            if until > now + timedelta(days=3):
                continue
            expired = until <= now
            notice(
                subscription,
                f"expiry:{subscription.pk}:{until.isoformat()}:{expired}",
                "access_expired" if expired else "access_expiring",
                "Your billing period has expired. Open Billing to renew access."
                if expired
                else "Your billing period ends within three days. Review payment or automatic renewal in Billing.",
            )
    finally:
        if owns_connection:
            close_old_connections()
