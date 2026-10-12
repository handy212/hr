"""
Admin-facing views for the FC-series terminal gateway.

Device-facing protocol endpoints live in ``device_gateway.views``; this module
is the staff UI for managing devices, queueing sync/remote commands, and
inspecting punch logs.
"""

import json
import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from base.methods import get_pagination
from employee.models import Employee
from horilla.decorators import login_required, permission_required
from horilla.filters import HorillaPaginator

from .attendance_sync import PASSING_RECORD_TYPES
from .forms import TerminalDeviceForm, TerminalEmployeeForm
from .models import (
    TerminalCommand,
    TerminalDevice,
    TerminalPunchLog,
    TerminalSyncTask,
)

logger = logging.getLogger(__name__)


def device_detail_context(device, mapping_form=None):
    punches = list(device.punch_logs.all()[:10])
    for punch in punches:
        punch.can_retry_attendance = (
            not punch.attendance_created and punch.record_type in PASSING_RECORD_TYPES
        )
    return {
        "device": device,
        "device_status_json": json.dumps(device.device_status, indent=2),
        "work_parameters_json": json.dumps(device.work_parameters, indent=2),
        "recent_punches": punches,
        "total_punches": device.punch_logs.count(),
        "pending_tasks": device.sync_tasks.filter(status="pending").count(),
        "pending_commands": device.commands.filter(status="pending").count(),
        "employee_mappings": device.terminal_employees.select_related("employee").order_by("device_user_id"),
        "mapping_form": mapping_form if mapping_form is not None else TerminalEmployeeForm(device=device),
        "unmapped_punches": device.punch_logs.filter(
            employee__isnull=True, attendance_created=False,
            record_type__in=PASSING_RECORD_TYPES,
        ).count(),
    }


def paginate(request, queryset):
    """Paginate a queryset using the Horilla paginator."""
    paginator = HorillaPaginator(queryset, get_pagination())
    return paginator.get_page(request.GET.get("page"))


@login_required
@permission_required("device_gateway.view_terminaldevice")
def device_list(request):
    """List all registered FC-series terminals."""
    devices = TerminalDevice.objects.all().order_by("-created_at")
    context = {
        "devices": paginate(request, devices),
        "total_devices": devices.count(),
        "online_devices": devices.filter(is_online=True).count(),
        "offline_devices": devices.filter(is_online=False).count(),
        "inactive_devices": devices.filter(is_active=False).count(),
        "pd": request.GET.urlencode(),
    }
    return render(request, "device_gateway/device_list.html", context)


@login_required
@permission_required("device_gateway.add_terminaldevice")
def device_create(request):
    """Register a new terminal by SN before it heartbeats in."""
    form = TerminalDeviceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Terminal device added successfully."))
        return redirect("terminal-device-list")
    return render(request, "device_gateway/device_form.html", {"form": form})


@login_required
@permission_required("device_gateway.change_terminaldevice")
def device_edit(request, pk):
    """Edit a terminal's registration details."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    form = TerminalDeviceForm(request.POST or None, instance=device)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Terminal device updated successfully."))
        return redirect("terminal-device-detail", pk=device.pk)
    return render(
        request,
        "device_gateway/device_form.html",
        {"form": form, "device": device},
    )


@login_required
@permission_required("device_gateway.view_terminaldevice")
def device_detail(request, pk):
    """Show device state and the queueable remote actions."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    context = device_detail_context(device)
    return render(request, "device_gateway/device_detail.html", context)


@login_required
@permission_required("device_gateway.change_terminaldevice")
def employee_mapping_create(request, pk):
    device = get_object_or_404(TerminalDevice, pk=pk)
    if request.method != "POST":
        return redirect("terminal-device-detail", pk=pk)
    form = TerminalEmployeeForm(request.POST, device=device)
    if form.is_valid():
        mapping = form.save(commit=False)
        mapping.device = device
        mapping.save()
        messages.success(request, _("Terminal UserID mapped to employee."))
        return redirect("terminal-device-detail", pk=pk)
    context = device_detail_context(device, mapping_form=form)
    return render(request, "device_gateway/device_detail.html", context, status=400)


@login_required
@permission_required("device_gateway.change_terminaldevice")
def punch_retry(request, pk, log_id):
    device = get_object_or_404(TerminalDevice, pk=pk)
    if request.method != "POST":
        return redirect("terminal-device-detail", pk=pk)
    log = get_object_or_404(TerminalPunchLog, pk=log_id, device=device)
    if log.attendance_created:
        messages.info(request, _("Attendance was already created for this punch."))
    else:
        from .attendance_sync import process_punch

        result = process_punch(device, log.raw, retry=True)
        if result == "created":
            messages.success(request, _("Attendance created from the saved punch."))
        elif result == "unmapped":
            messages.error(request, _("Map this UserID to an employee first."))
        else:
            messages.error(request, _("This punch cannot create attendance: %(reason)s") % {"reason": result})
    return redirect("terminal-device-detail", pk=pk)


DEVICE_ACTIONS = {
    "sync-all": _("Employee sync queued for %(count)d employees."),
    "delete-all": _("Delete-all personnel task queued."),
    "open-door": _("Remote open-door command queued."),
    "reboot": _("Reboot command queued."),
    "snapshot": _("Snapshot command queued."),
}


@login_required
@permission_required("device_gateway.change_terminaldevice")
def device_action(request, pk, action):
    """Queue a sync task or remote command for the device (POST only)."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    if request.method != "POST":
        return redirect("terminal-device-detail", pk=device.pk)

    if action == "sync-all":
        employees = Employee.objects.filter(is_active=True)
        if device.company_id_id:
            employees = employees.filter(employee_work_info__company_id=device.company_id)
        already_queued = set(
            TerminalSyncTask.objects.filter(
                device=device, action="add", status="pending"
            ).values_list("employee_id", flat=True)
        )
        tasks = [
            TerminalSyncTask(device=device, employee=employee, action="add")
            for employee in employees
            if employee.pk not in already_queued
        ]
        TerminalSyncTask.objects.bulk_create(tasks)
        messages.success(
            request, DEVICE_ACTIONS[action] % {"count": len(tasks)}
        )
    elif action == "delete-all":
        TerminalSyncTask.objects.create(device=device, action="delete_all")
        messages.success(request, DEVICE_ACTIONS[action])
    elif action in ("open-door", "reboot", "snapshot"):
        payload = {
            "open-door": {"Opendoor": 1},
            "reboot": {"Restart": 1},
            "snapshot": {"Snapshoot": 1},
        }[action]
        TerminalCommand.objects.create(device=device, payload=payload)
        messages.success(request, DEVICE_ACTIONS[action])
    else:
        messages.error(request, _("Unknown action."))

    return redirect("terminal-device-detail", pk=device.pk)


@login_required
@permission_required("device_gateway.view_terminalpunchlog")
def punch_log_list(request):
    """Paginated punch log list, filterable by device."""
    logs = TerminalPunchLog.objects.select_related("device", "employee")
    device_id = request.GET.get("device")
    if device_id:
        logs = logs.filter(device_id=device_id)
    context = {
        "logs": paginate(request, logs),
        "devices": TerminalDevice.objects.all(),
        "selected_device": device_id or "",
        "pd": request.GET.urlencode(),
    }
    return render(request, "device_gateway/punch_logs.html", context)


@login_required
@permission_required("device_gateway.view_terminalsynctask")
def sync_task_list(request):
    """Paginated sync task list, filterable by device and status."""
    tasks = TerminalSyncTask.objects.select_related("device", "employee")
    device_id = request.GET.get("device")
    if device_id:
        tasks = tasks.filter(device_id=device_id)
    status = request.GET.get("status")
    if status:
        tasks = tasks.filter(status=status)
    context = {
        "tasks": paginate(request, tasks.order_by("-created_at")),
        "devices": TerminalDevice.objects.all(),
        "selected_device": device_id or "",
        "selected_status": status or "",
        "status_choices": TerminalSyncTask.STATUS_CHOICES,
        "pd": request.GET.urlencode(),
    }
    return render(request, "device_gateway/sync_tasks.html", context)


@login_required
@permission_required("device_gateway.change_terminalsynctask")
def sync_task_retry(request, pk):
    """Reset a failed sync task to pending (POST only)."""
    task = get_object_or_404(TerminalSyncTask, pk=pk)
    if request.method == "POST":
        if task.status == "failed":
            task.status = "pending"
            task.save(update_fields=["status"])
            messages.success(request, _("Sync task re-queued."))
        else:
            messages.info(request, _("Only failed tasks can be retried."))
    return redirect("terminal-sync-tasks")
