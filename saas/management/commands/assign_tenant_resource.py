"""Explicit recovery for legacy resources whose ownership cannot be inferred."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ObjectDoesNotExist
from saas.models import Tenant
from saas.ownership import RESOURCE_FIELDS, assign_resource


class Command(BaseCommand):
    help = 'Assign a legacy resource to a tenant with an explicit platform actor.'

    def add_arguments(self, parser):
        parser.add_argument('model', choices=sorted(RESOURCE_FIELDS))
        parser.add_argument('pk')
        parser.add_argument('tenant_slug')
        parser.add_argument('--actor', required=True, help='Active platform superuser username')

    def handle(self, *args, **options):
        try:
            actor = get_user_model().objects.get(username=options['actor'],is_active=True,is_superuser=True)
            tenant = Tenant.objects.get(slug=options['tenant_slug'],deleted_at__isnull=True)
            model = apps.get_model(options['model'])
            resource = model.objects.unscoped().get(pk=options['pk'])
            assign_resource(resource=resource,company=tenant.company,actor=actor)
        except (LookupError, ValueError, PermissionError, ObjectDoesNotExist) as error:
            raise CommandError('Check the resource, tenant and active platform actor.') from error
        self.stdout.write(self.style.SUCCESS('Resource ownership assigned.'))
