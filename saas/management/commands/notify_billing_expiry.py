from django.core.management.base import BaseCommand
from saas.billing_jobs import notify_expiring_subscriptions


class Command(BaseCommand):
    help = "Generate deduplicated in-app billing expiry notices."

    def handle(self, *args, **options):
        notify_expiring_subscriptions()
        self.stdout.write(self.style.SUCCESS("Billing expiry notices updated."))
