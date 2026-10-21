"""Validated inputs shared by platform provisioning entry points."""

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from saas.models import Tenant
from employee.models import Employee
from horilla_auth.models import HorillaUser


class TenantProvisionForm(forms.Form):
    company_name = forms.CharField(label=_("Company name"), max_length=50)
    slug = forms.SlugField(
        label=_("Tenant slug"),
        max_length=63,
        help_text=_(
            "Unique account identifier. Letters, numbers, hyphens and underscores."
        ),
    )
    address = forms.CharField(label=_("Address"), max_length=255)
    country = forms.CharField(label=_("Country"), max_length=50)
    state = forms.CharField(label=_("State / province"), max_length=50)
    city = forms.CharField(label=_("City"), max_length=50)
    zip = forms.CharField(label=_("Postal code"), max_length=20)
    status = forms.ChoiceField(
        label=_("Status"), choices=Tenant.Status.choices, initial=Tenant.Status.TRIAL
    )
    admin_first_name = forms.CharField(label=_("Admin first name"), max_length=150)
    admin_last_name = forms.CharField(
        label=_("Admin last name"), max_length=150, required=False
    )
    admin_email = forms.EmailField(label=_("Admin email / login"), max_length=150)
    admin_phone = forms.CharField(
        label=_("Admin phone"),
        max_length=25,
        validators=Employee._meta.get_field("phone").validators,
    )
    admin_password = forms.CharField(
        label=_("Temporary password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_(
            "Share securely. The admin must change this at first login. No email is sent."
        ),
    )

    def __init__(self, *args, **kwargs):
        # The shared country script owns id_country/id_state select widgets.
        # These are plain-text account fields, so keep their DOM IDs distinct.
        kwargs.setdefault("auto_id", "tenant_%s")
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs["class"] = "oh-input w-100"
            field_id = self[name].auto_id
            description_ids = []
            if field.help_text:
                description_ids.append(f"{field_id}_helptext")
            if self.is_bound and name in self.errors:
                description_ids.append(f"{field_id}_error")
                field.widget.attrs["aria-invalid"] = "true"
            if description_ids:
                field.widget.attrs["aria-describedby"] = " ".join(description_ids)

    def clean_slug(self):
        slug = self.cleaned_data["slug"].lower()
        if Tenant.objects.filter(slug=slug).exists():
            raise ValidationError(_("This tenant slug is already in use."))
        return slug

    def clean_admin_email(self):
        email = self.cleaned_data["admin_email"].lower()
        if (
            HorillaUser.objects.filter(email__iexact=email).exists()
            or HorillaUser.objects.filter(username__iexact=email).exists()
            or Employee.objects.unscoped().filter(email__iexact=email).exists()
        ):
            raise ValidationError(_("This email already belongs to an account."))
        return email

    def clean(self):
        data = super().clean()
        if data.get("admin_password"):
            user = HorillaUser(
                username=data.get("admin_email", ""),
                email=data.get("admin_email", ""),
                first_name=data.get("admin_first_name", ""),
                last_name=data.get("admin_last_name", ""),
            )
            try:
                validate_password(data["admin_password"], user=user)
            except ValidationError as error:
                self.add_error("admin_password", error)
        return data


class TenantStatusForm(forms.Form):
    status = forms.ChoiceField(choices=Tenant.Status.choices)


class TenantUpdateForm(forms.ModelForm):
    company_name = forms.CharField(label=_("Company name"), max_length=50)
    address = forms.CharField(label=_("Address"), max_length=255)
    country = forms.CharField(label=_("Country"), max_length=50)
    state = forms.CharField(label=_("State / province"), max_length=50)
    city = forms.CharField(label=_("City"), max_length=50)
    zip = forms.CharField(label=_("Postal code"), max_length=20)

    company_fields = ("address", "country", "state", "city", "zip")

    class Meta:
        model = Tenant
        fields = (
            "company_name",
            "primary_admin",
            "slug",
            "status",
            "address",
            "country",
            "state",
            "city",
            "zip",
        )

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("auto_id", "tenant_%s")
        super().__init__(*args, **kwargs)
        from employee.models import EmployeeWorkInformation

        user_ids = (
            EmployeeWorkInformation.objects.unscoped()
            .filter(company_id=self.instance.company_id, employee_id__is_active=True)
            .values_list("employee_id__employee_user_id", flat=True)
        )
        self.fields["primary_admin"].queryset = HorillaUser.objects.filter(
            pk__in=user_ids, is_active=True, is_superuser=False
        )
        self.fields["primary_admin"].help_text = _(
            "Choose the company user to impersonate. Their existing permissions are preserved."
        )
        self.initial["company_name"] = self.instance.company.company
        for name in self.company_fields:
            self.initial[name] = getattr(self.instance.company, name)

    def clean_slug(self):
        return self.cleaned_data["slug"].lower()
