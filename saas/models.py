"""Platform-owned account metadata; Company remains the data-isolation root."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Tenant(models.Model):
    class Status(models.TextChoices):
        TRIAL = "trial", _("Trial")
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")

    company = models.OneToOneField(
        "base.Company", on_delete=models.PROTECT, related_name="tenant"
    )
    slug = models.SlugField(max_length=63, unique=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.TRIAL
    )
    primary_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_tenants",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="provisioned_tenants",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "base_tenant"  # Retain the existing table and primary keys.
        ordering = ("-created_at", "-pk")
        # All management goes through explicit platform-only endpoints.
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["trial", "active", "suspended"]),
                name="tenant_valid_status",
            )
        ]

    def __str__(self):
        return self.slug


class ResourceOwnership(models.Model):
    """SaaS ownership extension for upstream models with no company field."""
    company = models.ForeignKey('base.Company', on_delete=models.CASCADE)
    employee_tag = models.ForeignKey('employee.EmployeeTag', null=True, blank=True, on_delete=models.CASCADE, related_name='saas_owners')
    action_type = models.ForeignKey('employee.Actiontype', null=True, blank=True, on_delete=models.CASCADE, related_name='saas_owners')
    skill = models.ForeignKey('recruitment.Skill', null=True, blank=True, on_delete=models.CASCADE, related_name='saas_owners')
    anonymous_feedback = models.ForeignKey('pms.AnonymousFeedback', null=True, blank=True, on_delete=models.CASCADE, related_name='saas_owners')
    encashment_settings = models.ForeignKey('payroll.EncashmentGeneralSettings', null=True, blank=True, on_delete=models.CASCADE, related_name='saas_owners')
    mail_automation = models.ForeignKey('horilla_automations.MailAutomation', null=True, blank=True, on_delete=models.CASCADE, related_name='saas_owners')

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(employee_tag__isnull=False, action_type__isnull=True, skill__isnull=True, anonymous_feedback__isnull=True, encashment_settings__isnull=True, mail_automation__isnull=True)
                    | models.Q(employee_tag__isnull=True, action_type__isnull=False, skill__isnull=True, anonymous_feedback__isnull=True, encashment_settings__isnull=True, mail_automation__isnull=True)
                    | models.Q(employee_tag__isnull=True, action_type__isnull=True, skill__isnull=False, anonymous_feedback__isnull=True, encashment_settings__isnull=True, mail_automation__isnull=True)
                    | models.Q(employee_tag__isnull=True, action_type__isnull=True, skill__isnull=True, anonymous_feedback__isnull=False, encashment_settings__isnull=True, mail_automation__isnull=True)
                    | models.Q(employee_tag__isnull=True, action_type__isnull=True, skill__isnull=True, anonymous_feedback__isnull=True, encashment_settings__isnull=False, mail_automation__isnull=True)
                    | models.Q(employee_tag__isnull=True, action_type__isnull=True, skill__isnull=True, anonymous_feedback__isnull=True, encashment_settings__isnull=True, mail_automation__isnull=False)
                ), name='saas_resource_one_target',
            ),
            models.UniqueConstraint(fields=['company', 'employee_tag'], name='saas_owner_employee_tag'),
            models.UniqueConstraint(fields=['company', 'action_type'], name='saas_owner_action_type'),
            models.UniqueConstraint(fields=['company', 'skill'], name='saas_owner_skill'),
            models.UniqueConstraint(fields=['company', 'anonymous_feedback'], name='saas_owner_feedback'),
            models.UniqueConstraint(fields=['company', 'encashment_settings'], name='saas_owner_encashment'),
            models.UniqueConstraint(fields=['company', 'mail_automation'], name='saas_owner_automation'),
        ]


class BillingPlan(models.Model):
    """Global commercial catalog; accessible only through platform authorization."""
    class Interval(models.TextChoices):
        MONTHLY = 'monthly', 'Monthly'
        ANNUALLY = 'annually', 'Annually'

    CURRENCIES = [(code, code) for code in ('NGN', 'GHS', 'ZAR', 'KES', 'USD', 'XOF')]
    name = models.CharField(max_length=100)
    amount = models.PositiveBigIntegerField(help_text='Amount in minor currency units.')
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    interval = models.CharField(max_length=10, choices=Interval.choices)
    active = models.BooleanField(default=True)
    provider_code = models.CharField(max_length=100, blank=True, unique=True, null=True)
    provider_domain = models.CharField(max_length=4, blank=True)
    publication_state = models.CharField(max_length=10, default='draft', choices=[
        ('draft', 'Draft'), ('pending', 'Publishing'), ('published', 'Published'),
        ('uncertain', 'Needs reconciliation')])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        ordering = ('-created_at', '-pk')
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='saas_plan_positive_price'),
            models.CheckConstraint(condition=models.Q(interval__in=['monthly', 'annually']), name='saas_plan_interval'),
        ]

    @property
    def price(self):
        from decimal import Decimal
        return Decimal(self.amount) / 100

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if original.publication_state != 'draft' and any(
                getattr(self, field) != getattr(original, field)
                for field in ('name', 'amount', 'currency', 'interval')
            ):
                raise ValidationError('Published or pending plan terms are frozen. Create a new plan.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
