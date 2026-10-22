"""
Forms for the device_gateway admin UI.
"""

import json

from base.forms import ModelForm
from django import forms
from django.utils.translation import gettext_lazy as _

from .models import TerminalDevice, TerminalEmployee
from .protocol import PARAMETER_GROUPS


class TerminalDeviceForm(ModelForm):
    """Create/edit a registered FC-series terminal."""

    class Meta:
        model = TerminalDevice
        fields = [
            "sn",
            "name",
            "protocol",
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


class TerminalEmployeeAccessForm(ModelForm):
    """Per-device access rules for an already mapped employee."""

    class Meta:
        model = TerminalEmployee
        fields = [
            "card_num",
            "access_type",
            "expiration_date",
            "open_times",
            "timegroup",
        ]
        widgets = {
            "expiration_date": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def clean_card_num(self):
        card = (self.cleaned_data.get("card_num") or "").strip()
        if card and not card.isdigit():
            raise forms.ValidationError(_("Card number must contain digits only."))
        return card


class TerminalParametersForm(forms.Form):
    """Editable subset of the device working parameters (spec §Device operating parameters)."""

    def __init__(self, *args, device, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = device
        params = device.work_parameters or {}
        self.groups = []
        for title, fields in PARAMETER_GROUPS:
            bound = []
            for key, label, kind, extra in fields:
                if key not in params:
                    continue  # only expose what this firmware reports
                self.fields[key] = self._build_field(label, kind, extra, params[key])
                bound.append(key)
            if bound:
                self.groups.append((title, bound))

    @staticmethod
    def _build_field(label, kind, extra, current):
        css = {"class": "oh-input w-100"}
        if kind == "bool":
            return forms.BooleanField(
                label=label, required=False, initial=bool(current)
            )
        if kind == "int":
            return forms.IntegerField(
                label=label,
                initial=current,
                min_value=extra.get("min"),
                max_value=extra.get("max"),
                widget=forms.NumberInput(attrs=css),
            )
        if kind == "float":
            return forms.FloatField(
                label=label, initial=current, widget=forms.NumberInput(attrs=css)
            )
        if kind == "choice":
            return forms.TypedChoiceField(
                label=label,
                choices=extra["choices"],
                coerce=int,
                initial=current,
                widget=forms.Select(attrs=css),
            )
        if kind == "json":
            return forms.CharField(
                label=label,
                required=False,
                initial=json.dumps(current, indent=1),
                widget=forms.Textarea(attrs={**css, "rows": 4}),
            )
        return forms.CharField(
            label=label,
            required=False,
            initial=current,
            widget=forms.TextInput(attrs=css),
        )

    def clean(self):
        cleaned = super().clean()
        for name in list(cleaned):
            if self.fields[name].widget.__class__ is forms.Textarea:
                try:
                    cleaned[name] = json.loads(cleaned[name] or "null")
                except ValueError:
                    self.add_error(name, _("Enter valid JSON."))
        return cleaned

    def changed_parameters(self):
        params = self.device.work_parameters or {}
        changes = {}
        for name, value in self.cleaned_data.items():
            current = params.get(name)
            if isinstance(value, bool) and not isinstance(current, bool):
                value = int(value)  # the device reports flags as 0/1
            if value != current:
                changes[name] = value
        return changes
