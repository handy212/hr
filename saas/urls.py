from django.urls import path
from saas import views

# Preserve public URLs and reverse names across the ownership move.
urlpatterns = [
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
