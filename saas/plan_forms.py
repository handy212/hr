"""Exact major-unit input for the platform's minor-unit price storage."""

from decimal import Decimal
from django import forms
from saas.models import BillingPlan


class BillingPlanForm(forms.ModelForm):
    price = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_value=Decimal("99999999.99"),
        decimal_places=2,
        max_digits=10,
        help_text="Price in the selected currency, for example 199.99.",
    )

    class Meta:
        model = BillingPlan
        fields = ("name", "price", "currency", "interval", "active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["price"] = self.instance.price

    def clean(self):
        data = super().clean()
        if data.get("price") is not None:
            self.instance.amount = int(data["price"] * 100)
        return data
