"""
Device gateway sidebar configuration.
"""

from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from attendance.sidebar import SUBMENUS

terminal_submenu = {
    "menu": _("Terminal Devices"),
    "redirect": reverse_lazy("terminal-device-list"),
    "accessibility": "device_gateway.sidebar.terminal_device_accessibility",
}

# Keep the existing Attendance and Biometric menu order intact. App startup can
# import this module more than once in development, so avoid duplicate entries.
if not any(
    submenu.get("accessibility") == terminal_submenu["accessibility"]
    for submenu in SUBMENUS
):
    SUBMENUS.append(terminal_submenu)


def terminal_device_accessibility(request, submenu, user_perms, *args, **kwargs):
    """Show the entry to users allowed to view terminal devices."""
    return request.user.has_perm("device_gateway.view_terminaldevice")
