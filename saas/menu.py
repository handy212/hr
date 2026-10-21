from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from horilla.menu import settings_menu


@settings_menu.register
class PlatformSettings:
    title = _("Platform")
    order = 1
    items = [
        {
            "label": _("Tenants"),
            "url": reverse_lazy("tenant-list"),
            "full_page": True,
            "accessibility": lambda request, *_args, **_kwargs: (
                request.user.is_superuser
            ),
        },
    ]


def can_manage_billing(request, *_args, **_kwargs):
    from saas.models import Tenant

    user = request.user
    return bool(
        user.is_authenticated
        and user.is_active
        and not user.is_superuser
        and not getattr(request, "impersonation_tenant", None)
        and Tenant.objects.filter(primary_admin=user, deleted_at__isnull=True).exists()
    )


@settings_menu.register
class TenantBillingSettings:
    title = _("Subscription")
    order = 2
    items = [
        {
            "label": _("Billing"),
            "url": reverse_lazy("billing-home"),
            "full_page": True,
            "accessibility": can_manage_billing,
        }
    ]
