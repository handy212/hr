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
