from django.urls import path
from saas import views, plan_views, billing_views

# Preserve public URLs and reverse names across the ownership move.
urlpatterns = [
    path(
        "billing/tenants/<int:pk>/payments/<str:reference>/close/",
        billing_views.tenant_close_checkout,
        name="tenant-close-checkout",
    ),
    path(
        "billing/tenants/<int:pk>/reconcile/",
        billing_views.tenant_reconcile_renewal,
        name="tenant-reconcile-renewal",
    ),
    path(
        "billing/tenants/<int:pk>/notices/read/",
        billing_views.tenant_notices_read,
        name="tenant-notices-read",
    ),
    path("billing/", billing_views.billing_home, name="billing-home"),
    path(
        "billing/paystack/callback/",
        billing_views.paystack_callback,
        name="paystack-callback",
    ),
    path(
        "billing/paystack/webhook/",
        billing_views.paystack_webhook,
        name="paystack-webhook",
    ),
    path(
        "billing/tenants/<int:pk>/", billing_views.tenant_billing, name="tenant-billing"
    ),
    path(
        "billing/tenants/<int:pk>/enroll/",
        billing_views.tenant_enroll,
        name="tenant-enroll",
    ),
    path(
        "billing/tenants/<int:pk>/checkout/",
        billing_views.tenant_checkout,
        name="tenant-checkout",
    ),
    path(
        "billing/tenants/<int:pk>/payments/<str:reference>/verify/",
        billing_views.tenant_verify,
        name="tenant-payment-verify",
    ),
    path(
        "billing/tenants/<int:pk>/cancel/",
        billing_views.tenant_cancel_renewal,
        name="tenant-cancel-renewal",
    ),
    path(
        "settings/plans/<int:pk>/reconcile/",
        plan_views.plan_reconcile,
        name="plan-reconcile",
    ),
    path("settings/plans/", plan_views.plan_list, name="plan-list"),
    path("settings/plans/create/", plan_views.plan_edit, name="plan-create"),
    path("settings/plans/<int:pk>/", plan_views.plan_edit, name="plan-edit"),
    path(
        "settings/plans/<int:pk>/publish/", plan_views.plan_publish, name="plan-publish"
    ),
    path(
        "settings/plans/<int:pk>/archive/", plan_views.plan_archive, name="plan-archive"
    ),
    path(
        "settings/tenants/impersonation/stop/",
        views.tenant_impersonation_stop,
        name="tenant-impersonation-stop",
    ),
    path(
        "settings/tenants/<int:pk>/impersonate/",
        views.tenant_impersonate,
        name="tenant-impersonate",
    ),
    path("settings/tenants/", views.tenant_list, name="tenant-list"),
    path("settings/tenants/create/", views.tenant_create, name="tenant-create"),
    path("settings/tenants/<int:pk>/", views.tenant_detail, name="tenant-detail"),
    path("settings/tenants/<int:pk>/edit/", views.tenant_edit, name="tenant-edit"),
    path(
        "settings/tenants/<int:pk>/delete/", views.tenant_delete, name="tenant-delete"
    ),
    path(
        "settings/tenants/<int:pk>/status/", views.tenant_status, name="tenant-status"
    ),
]
