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
        "devices/<int:pk>/unmap-employee/<int:mapping_id>/",
        views_admin.employee_mapping_delete,
        name="terminal-employee-unmap",
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
        "devices/<int:pk>/delete/",
        views_admin.device_delete,
        name="terminal-device-delete",
    ),
    path(
        "devices/<int:pk>/parameters/",
        views_admin.device_parameters,
        name="terminal-device-parameters",
    ),
    path(
        "devices/<int:pk>/activity/",
        views_admin.device_activity,
        name="terminal-device-activity",
    ),
    path(
        "devices/<int:pk>/commands/<int:command_id>/cancel/",
        views_admin.command_cancel,
        name="terminal-command-cancel",
    ),
    path(
        "devices/<int:pk>/mappings/<int:mapping_id>/edit/",
        views_admin.employee_mapping_edit,
        name="terminal-employee-edit",
    ),
    path(
        "devices/<int:pk>/mappings/<int:mapping_id>/<str:action>/",
        views_admin.employee_mapping_action,
        name="terminal-employee-action",
    ),
    path(
        "devices/<int:pk>/roster/<int:user_id>/remove/",
        views_admin.roster_remove,
        name="terminal-roster-remove",
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
    path(
        "sync-tasks/retry-all/",
        views_admin.sync_task_retry_all,
        name="terminal-sync-task-retry-all",
    ),
]
