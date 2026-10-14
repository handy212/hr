"""
Forms for the device_gateway admin UI.
"""

from base.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from .models import TerminalDevice, TerminalEmployee


class TerminalDeviceForm(ModelForm):
    """Create/edit a registered FC-series terminal."""

    class Meta:
        model = TerminalDevice
        fields = [
            "sn",
            "name",
            "company_id",
            "connect_password",
            "direction",
            "is_active",
        ]


class TerminalEmployeeForm(ModelForm):
    """Map the UserID already stored on a terminal to an employee."""

    class Meta:
        model = TerminalEmployee
        fields = ["device_user_id", "employee"]

    def __init__(self, *args, device, **kwargs):
        super().__init__(*args, **kwargs)
        employees = self.fields["employee"].queryset.filter(is_active=True)
        if device.company_id_id:
            employees = employees.filter(
                employee_work_info__company_id=device.company_id
            )
        self.fields["employee"].queryset = employees.order_by(
            "employee_first_name", "employee_last_name"
        )
        self.device = device
        self.fields["device_user_id"].widget.attrs.update(
            {
                "placeholder": _("e.g. 1001"),
                "class": "oh-input w-100",
                "id": "id_device_user_id",
            }
        )
        self.fields["employee"].empty_label = _("--- Select an Employee ---")


    def clean(self):
        cleaned = super().clean()
        employee = cleaned.get("employee")
        user_id = cleaned.get("device_user_id")
        if employee and TerminalEmployee.objects.filter(
            device=self.device, employee=employee
        ).exists():
            self.add_error("employee", "This employee is already mapped on this terminal.")
        if user_id is not None and TerminalEmployee.objects.filter(
            device=self.device, device_user_id=user_id
        ).exists():
            self.add_error("device_user_id", "This UserID is already mapped on this terminal.")
        return cleaned
