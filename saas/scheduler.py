"""Register billing notices with Horilla's single dedicated scheduler."""

from horilla.scheduling import register_job
from saas.billing_jobs import notify_expiring_subscriptions

register_job(notify_expiring_subscriptions, "interval", hours=1)
