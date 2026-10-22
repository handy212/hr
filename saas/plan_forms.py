"""Exact major-unit input for the platform's minor-unit price storage."""

from decimal import Decimal
from django import forms
from saas.models import BillingPlan
from saas.features import (
    FEATURE_CHOICES,
    all_features,
    set_plan_features,
    validate_features,
)
from django.db import transaction
from django.core.exceptions import ValidationError


class BillingPlanForm(forms.ModelForm):
    price = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_value=Decimal("99999999.99"),
        decimal_places=2,
        max_digits=10,
        help_text="Price in the selected currency, for example 199.99.",
    )

    features = forms.MultipleChoiceField(
        choices=FEATURE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        initial=all_features,
        help_text="Employee records, organization, authentication and billing are always included. Payroll requires attendance and leave; onboarding requires recruitment; devices require attendance.",
    )

    class Meta:
        model = BillingPlan
        fields = ("name", "price", "currency", "interval", "features", "active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["price"] = self.instance.price
            self.initial["features"] = list(
                self.instance.entitlements.values_list("feature", flat=True)
            )

    def clean_features(self):
        selected = validate_features(self.cleaned_data["features"])
        if self.instance.pk and self.instance.publication_state != "draft":
            existing = set(self.instance.entitlements.values_list("feature", flat=True))
            if existing != selected:
                raise ValidationError(
                    "Published or pending plan features are frozen. Create a new plan."
                )
        return sorted(selected)

    def save(self, commit=True):
        if not commit:
            plan = super().save(commit=False)
            self.save_m2m = lambda: set_plan_features(
                plan, self.cleaned_data["features"]
            )
            return plan
        with transaction.atomic():
            plan = super().save(commit=False)
            if plan.pk:
                BillingPlan.objects.select_for_update().get(pk=plan.pk)
            plan.save()
            set_plan_features(plan, self.cleaned_data["features"])
            return plan

    def clean(self):
        data = super().clean()
        if data.get("price") is not None:
            self.instance.amount = int(data["price"] * 100)
        return data
