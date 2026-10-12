"""
Admin UI URL patterns, mounted at ``device-gateway/`` by
``DeviceGatewayConfig.ready()``.
"""

from django.urls import path

from . import views_admin

urlpatterns = [
    path("", views_admin.device_list, name="terminal-device-list"),
    path("devices/add/", views_admin.device_create, name="terminal-device-add"),
    path(
        "devices/<int:pk>/edit/",
        views_admin.device_edit,
        name="terminal-device-edit",
    ),
    path(
        "devices/<int:pk>/",
        views_admin.device_detail,
        name="terminal-device-detail",
    ),
    path(
        "devices/<int:pk>/map-employee/",
        views_admin.employee_mapping_create,
        name="terminal-employee-map",
    ),
    path(
        "devices/<int:pk>/punches/<int:log_id>/retry/",
        views_admin.punch_retry,
        name="terminal-punch-retry",
    ),
    path(
        "devices/<int:pk>/action/<str:action>/",
        views_admin.device_action,
        name="terminal-device-action",
    ),
    path(
        "punch-logs/",
        views_admin.punch_log_list,
        name="terminal-punch-logs",
    ),
    path(
        "sync-tasks/",
        views_admin.sync_task_list,
        name="terminal-sync-tasks",
    ),
    path(
        "sync-tasks/<int:pk>/retry/",
        views_admin.sync_task_retry,
        name="terminal-sync-task-retry",
    ),
]
