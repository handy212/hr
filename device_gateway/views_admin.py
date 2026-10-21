"""
Admin-facing views for the FC-series terminal gateway.

Device-facing protocol endpoints live in ``device_gateway.views``; this module
is the staff UI for managing devices, queueing sync/remote commands, and
inspecting punch logs.
"""

import json
import logging
import re

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from base.methods import get_pagination
from employee.models import Employee
from horilla.decorators import login_required, permission_required
from horilla.filters import HorillaPaginator

from .attendance_sync import PASSING_RECORD_TYPES
from .forms import (
    TerminalDeviceForm,
    TerminalEmployeeAccessForm,
    TerminalEmployeeForm,
    TerminalParametersForm,
)
from .models import (
    TerminalCommand,
    TerminalDevice,
    TerminalDevicePerson,
    TerminalEmployee,
    TerminalPunchLog,
    TerminalSnapshot,
    TerminalSyncTask,
    TerminalSystemRecord,
)
from .protocol import (
    ALARM_EVENT_TYPES,
    READ_ONLY_PARAMETERS,
    REGISTER_RESULTS,
    REGISTER_TYPES,
    system_event_name,
)

logger = logging.getLogger(__name__)

RECORD_TYPE_METADATA = {
    1: {"name": _("Card"), "icon": "card-outline", "color": "info"},
    2: {"name": _("Fingerprint"), "icon": "finger-print-outline", "color": "primary"},
    3: {"name": _("Face"), "icon": "scan-outline", "color": "success"},
    4: {"name": _("Password"), "icon": "key-outline", "color": "warning"},
    5: {"name": _("Card + Fingerprint"), "icon": "finger-print-outline", "color": "primary"},
    6: {"name": _("Card + Password"), "icon": "card-outline", "color": "info"},
    7: {"name": _("Fingerprint + Password"), "icon": "finger-print-outline", "color": "primary"},
    8: {"name": _("Card + FP + Pass"), "icon": "finger-print-outline", "color": "primary"},
    9: {"name": _("Face + Password"), "icon": "scan-outline", "color": "success"},
    10: {"name": _("Face + Card"), "icon": "scan-outline", "color": "success"},
    11: {"name": _("Face + Fingerprint"), "icon": "scan-outline", "color": "success"},
    12: {"name": _("Remote Open"), "icon": "lock-open-outline", "color": "secondary"},
    13: {"name": _("Exit Button"), "icon": "exit-outline", "color": "secondary"},
    14: {"name": _("Card + Face + Pass"), "icon": "scan-outline", "color": "success"},
    19: {"name": _("Unregistered User"), "icon": "alert-circle-outline", "color": "danger"},
    24: {"name": _("Blacklisted"), "icon": "ban-outline", "color": "danger"},
    31: {"name": _("Dynamic QR"), "icon": "qr-code-outline", "color": "info"},
    32: {"name": _("Static QR"), "icon": "qr-code-outline", "color": "info"},
}


def decorate_punch(punch):
    """Attach human-readable presentation attributes to a punch log."""
    meta = RECORD_TYPE_METADATA.get(punch.record_type, {
        "name": _("Type #%(type)d") % {"type": punch.record_type},
        "icon": "shield-checkmark-outline",
        "color": "info" if punch.record_type in PASSING_RECORD_TYPES else "danger",
    })
    punch.record_type_name = meta["name"]
    punch.record_type_icon = meta["icon"]
    punch.record_type_color = meta["color"]
    punch.can_retry_attendance = (
        not punch.attendance_created and punch.record_type in PASSING_RECORD_TYPES
    )
    return punch


def unmapped_roster(device):
    mapped = device.terminal_employees.values_list("device_user_id", flat=True)
    return device.roster.exclude(user_id__in=mapped)


def device_detail_context(device, mapping_form=None, initial_mapping=None):
    punches = list(device.punch_logs.all()[:15])
    for punch in punches:
        decorate_punch(punch)

    unmapped_punches_qs = device.punch_logs.filter(
        employee__isnull=True,
        attendance_created=False,
        record_type__in=PASSING_RECORD_TYPES,
    )
    unmapped_user_ids = list(
        unmapped_punches_qs.exclude(user_id="").values_list("user_id", flat=True).distinct()[:10]
    )

    today = timezone.now().date()
    today_punches_count = device.punch_logs.filter(record_date__date=today).count()

    recent_tasks = list(
        device.sync_tasks.select_related("employee").order_by("-id")[:5]
    )
    recent_commands = list(
        device.commands.order_by("-id")[:5]
    )

    return {
        "device": device,
        "device_status_json": json.dumps(device.device_status, indent=2),
        "work_parameters_json": json.dumps(device.work_parameters, indent=2),
        "recent_punches": punches,
        "recent_tasks": recent_tasks,
        "recent_commands": recent_commands,
        "total_punches": device.punch_logs.count(),
        "today_punches": today_punches_count,
        "pending_tasks": device.sync_tasks.filter(status="pending").count(),
        "failed_tasks": device.sync_tasks.filter(status="failed").count(),
        "pending_commands": device.commands.filter(status="pending").count(),
        "recent_commands": device.commands.order_by("-created_at")[:5],
        "employee_mappings": (
            device.terminal_employees
            .select_related("employee", "employee__employee_work_info", "employee__employee_work_info__department_id")
            .order_by("device_user_id")
        ),
        "mapping_form": mapping_form if mapping_form is not None else TerminalEmployeeForm(
            device=device, initial=initial_mapping or None
        ),
        "storage": device.storage_info,
        "active_alarms": device.active_alarms,
        "roster_total": device.roster.count(),
        "roster_unmapped": unmapped_roster(device)[:20],
        "register_types": REGISTER_TYPES.items(),
        "door_actions": [
            ("keep-open", _("Keep open")),
            ("close-door", _("Exit keep-open")),
            ("lock-door", _("Lock")),
            ("unlock-door", _("Unlock")),
            ("close-alarm", _("Silence alarms")),
        ],
        "data_actions": [
            ("pull-people", _("Pull people from device")),
            ("repost-records", _("Re-upload records")),
            ("refresh-parameters", _("Refresh parameters")),
            ("sync-time", _("Sync time")),
        ],
        "unmapped_punches": unmapped_punches_qs.count(),
        "unmapped_user_ids": unmapped_user_ids,
    }


def paginate(request, queryset):
    """Paginate a queryset using the Horilla paginator."""
    paginator = HorillaPaginator(queryset, get_pagination())
    return paginator.get_page(request.GET.get("page"))


@login_required
@permission_required("device_gateway.view_terminaldevice")
def device_list(request):
    """List all registered FC-series terminals with search and filtering."""
    all_devices = TerminalDevice.objects.all()
    total_devices = all_devices.count()
    online_devices = all_devices.filter(is_online=True).count()
    offline_devices = all_devices.filter(is_online=False).count()
    inactive_devices = all_devices.filter(is_active=False).count()

    devices = all_devices.annotate(
        pending_tasks_count=Count("sync_tasks", filter=Q(sync_tasks__status="pending")),
        mapped_count=Count("terminal_employees", distinct=True),
    ).order_by("-created_at")

    search_query = (request.GET.get("search") or request.GET.get("q") or "").strip()
    if search_query:
        devices = devices.filter(
            Q(name__icontains=search_query) | Q(sn__icontains=search_query)
        )

    selected_status = (request.GET.get("status") or "").strip().lower()
    if selected_status == "online":
        devices = devices.filter(is_online=True)
    elif selected_status == "offline":
        devices = devices.filter(is_online=False)
    elif selected_status == "inactive":
        devices = devices.filter(is_active=False)
    elif selected_status == "active":
        devices = devices.filter(is_active=True)

    context = {
        "devices": paginate(request, devices),
        "total_devices": total_devices,
        "online_devices": online_devices,
        "offline_devices": offline_devices,
        "inactive_devices": inactive_devices,
        "search_query": search_query,
        "selected_status": selected_status,
        "pd": request.GET.urlencode(),
    }
    return render(request, "device_gateway/device_list.html", context)


@login_required
@permission_required("device_gateway.add_terminaldevice")
def device_create(request):
    """Register a new terminal by SN before it heartbeats in."""
    form = TerminalDeviceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        device = form.save()
        messages.success(request, _("Terminal device added successfully."))
        return redirect("terminal-device-detail", pk=device.pk)
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
    initial = {}
    if request.GET.get("uid", "").isdigit():
        initial["device_user_id"] = int(request.GET["uid"])
    context = device_detail_context(device, initial_mapping=initial)
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
        messages.success(
            request,
            _("Terminal UserID mapped to %(employee)s successfully.") % {"employee": mapping.employee},
        )
        return redirect("terminal-device-detail", pk=pk)
    context = device_detail_context(device, mapping_form=form)
    return render(request, "device_gateway/device_detail.html", context, status=400)


@login_required
@permission_required("device_gateway.change_terminaldevice")
def employee_mapping_delete(request, pk, mapping_id):
    """Remove a device-to-employee mapping."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    mapping = get_object_or_404(TerminalEmployee, pk=mapping_id, device=device)
    if request.method == "POST":
        emp_name = str(mapping.employee)
        uid = mapping.device_user_id
        mapping.delete()
        messages.success(
            request,
            _("Mapping for UserID %(uid)d (%(emp)s) removed.") % {"uid": uid, "emp": emp_name},
        )
    return redirect("terminal-device-detail", pk=pk)


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
            messages.error(
                request,
                _("This punch cannot create attendance: %(reason)s") % {"reason": result},
            )
    referer = request.META.get("HTTP_REFERER")
    if referer and ("punch-logs" in referer or "devices" in referer):
        return redirect(referer)
    return redirect("terminal-device-detail", pk=pk)


# Remote commands that map 1:1 onto a Device/RemoteCommand payload.
SIMPLE_COMMANDS = {
    "open-door": ({"Opendoor": 1}, _("Remote open-door command queued.")),
    "keep-open": ({"Opendoor": 2}, _("Keep-door-open command queued.")),
    "close-door": ({"Opendoor": 3}, _("Close-door command queued.")),
    "lock-door": ({"Opendoor": 4}, _("Lock-door command queued.")),
    "unlock-door": ({"Opendoor": 5}, _("Unlock-door command queued.")),
    "reboot": ({"Restart": 1}, _("Reboot command queued.")),
    "snapshot": ({"Snapshoot": 1}, _("Snapshot command queued.")),
    "close-alarm": ({"Closealarm": 1}, _("Close-alarm command queued.")),
    "repost-records": ({"RepostRecord": 1}, _("Record re-upload requested.")),
    "pull-people": ({"PushAllPeople": 1}, _("Personnel list upload requested.")),
    "clear-records": ({"ClearRecord": 1}, _("Clear-records command queued.")),
    "factory-reset": ({"Recover": 1}, _("Factory reset command queued.")),
}

ELEVATOR_COMMANDS = {
    "open": "OpenElevatorPort",
    "keep-open": "KeepOpenElevatorPort",
    "close": "CloseElevatorPort",
    "lock": "LockElevatorPort",
    "unlock": "UnlockElevatorPort",
}

URL_MAX = 1024


def queue_command(device, payload):
    return TerminalCommand.objects.create(device=device, payload=payload)


def sync_all_people(device):
    employees = Employee.objects.filter(is_active=True)
    if device.company_id_id:
        employees = employees.filter(employee_work_info__company_id=device.company_id)
    already_queued = set(
        TerminalSyncTask.objects.filter(
            device=device, action="add", status__in=("pending", "sent")
        ).values_list("employee_id", flat=True)
    )
    tasks = [
        TerminalSyncTask(device=device, employee=employee, action="add")
        for employee in employees
        if employee.pk not in already_queued
    ]
    TerminalSyncTask.objects.bulk_create(tasks)
    return len(tasks)


def _csv_ports(raw):
    ports = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not ports or not all(p.isdigit() and 1 <= int(p) <= 64 for p in ports):
        return None
    return ",".join(ports)


def _build_extended_command(request, action):
    """Commands that need user input. Returns (payload, error)."""
    post = request.POST
    if action == "register-credential":
        try:
            register_type = int(post.get("register_type"))
            user_id = int(post.get("user_id"))
        except (TypeError, ValueError):
            return None, _("Choose a credential type and a terminal UserID.")
        if register_type not in REGISTER_TYPES:
            return None, _("Unknown credential type.")
        ticket = {"RegisterType": register_type, "UserID": user_id}
        index = post.get("register_index", "").strip()
        if register_type in (3, 5):
            limit = 9 if register_type == 3 else 2
            low = 0 if register_type == 3 else 1
            if not index.isdigit() or not low <= int(index) <= limit:
                return None, _("Credential index must be %(low)d-%(high)d.") % {
                    "low": low, "high": limit}
            ticket["RegisterIndex"] = int(index)
        return {"RegisterIdentifyTicket": ticket}, None
    if action == "query-people":
        ids = [p.strip() for p in post.get("user_ids", "").split(",") if p.strip()]
        if not ids or not all(i.isdigit() and int(i) <= 4294967295 for i in ids):
            return None, _("Enter comma-separated numeric UserIDs.")
        return {"QueryPeople": [int(i) for i in ids]}, None
    if action == "push-firmware":
        url = post.get("url", "").strip()
        md5 = post.get("md5", "").strip().upper()
        version = post.get("version", "").strip()
        if not url.lower().startswith(("http://", "https://")) or len(url) > URL_MAX:
            return None, _("Enter a valid http(s) firmware URL.")
        if not re.fullmatch(r"[0-9A-F]{32}", md5):
            return None, _("MD5 must be 32 hexadecimal characters.")
        if not version:
            return None, _("Firmware version is required.")
        return {
            "PushSoftware": {
                "SoftwareURL": url, "SoftwareMD5": md5, "SoftwareVer": version
            }
        }, None
    if action == "push-file":
        url = post.get("url", "").strip()
        md5 = post.get("md5", "").strip().upper()
        try:
            file_type = int(post.get("file_type"))
            index = int(post.get("file_index") or 1)
        except (TypeError, ValueError):
            return None, _("Invalid file type or index.")
        delete = post.get("delete") == "1"
        if file_type not in (1, 2) or not 1 <= index <= (8 if file_type == 1 else 1):
            return None, _("Standby images use index 1-8; the boot image index 1.")
        if not delete:
            if not url.lower().startswith(("http://", "https://")) or len(url) > URL_MAX:
                return None, _("Enter a valid http(s) file URL.")
            if not re.fullmatch(r"[0-9A-F]{32}", md5):
                return None, _("MD5 must be 32 hexadecimal characters.")
        return {
            "PushSystemFile": [
                {
                    "FileURL": url,
                    "FileMD5": md5,
                    "FileType": file_type,
                    "FileIndex": index,
                    "IsDelete": int(delete),
                }
            ]
        }, None
    if action == "elevator":
        key = ELEVATOR_COMMANDS.get(post.get("operation"))
        ports = _csv_ports(post.get("ports"))
        if not key or not ports:
            return None, _("Choose an operation and ports 1-64, comma separated.")
        return {key: ports}, None
    return None, _("Unknown action.")


EXTENDED_COMMAND_MESSAGES = {
    "register-credential": _("Credential registration queued. Complete it on the terminal."),
    "query-people": _("Personnel query queued."),
    "push-firmware": _("Firmware upgrade queued."),
    "push-file": _("System file update queued."),
    "elevator": _("Elevator command queued."),
}

# Actions that can wipe data or brick a device need the delete permission.
DESTRUCTIVE_ACTIONS = {"delete-all", "clear-records", "factory-reset", "push-firmware"}


@login_required
@permission_required("device_gateway.change_terminaldevice")
def device_action(request, pk, action):
    """Queue a sync task or remote command for the device (POST only)."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    if request.method != "POST":
        return redirect("terminal-device-detail", pk=device.pk)

    if action in DESTRUCTIVE_ACTIONS and not request.user.has_perm(
        "device_gateway.delete_terminaldevice"
    ):
        messages.error(request, _("You do not have permission to run this action."))
        return redirect("terminal-device-detail", pk=device.pk)

    if action == "sync-all":
        count = sync_all_people(device)
        messages.success(
            request, _("Employee sync queued for %(count)d employees.") % {"count": count}
        )
    elif action == "delete-all":
        TerminalSyncTask.objects.create(device=device, action="delete_all")
        messages.success(request, _("Delete-all personnel task queued."))
    elif action in SIMPLE_COMMANDS:
        payload, message = SIMPLE_COMMANDS[action]
        queue_command(device, payload)
        messages.success(request, message)
    elif action in EXTENDED_COMMAND_MESSAGES:
        payload, error = _build_extended_command(request, action)
        if error:
            messages.error(request, error)
        else:
            queue_command(device, payload)
            messages.success(request, EXTENDED_COMMAND_MESSAGES[action])
    elif action == "refresh-parameters":
        device.params_refresh_requested = True
        device.save(update_fields=["params_refresh_requested"])
        messages.success(request, _("Parameter refresh requested."))
    elif action == "sync-time":
        device.work_parameters = {
            **(device.work_parameters or {}),
            "SystemTime": int(timezone.now().timestamp()),
        }
        device.params_dirty = True
        device.save(update_fields=["work_parameters", "params_dirty"])
        messages.success(request, _("Time synchronisation queued."))
    else:
        messages.error(request, _("Unknown action."))

    return redirect("terminal-device-detail", pk=device.pk)


@login_required
@permission_required("device_gateway.delete_terminaldevice")
def device_delete(request, pk):
    """Remove a terminal registration (its logs and mappings go with it)."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    if request.method == "POST":
        label = str(device)
        device.delete()
        messages.success(
            request, _("Terminal %(device)s deleted.") % {"device": label}
        )
        return redirect("terminal-device-list")
    return redirect("terminal-device-detail", pk=pk)


@login_required
@permission_required("device_gateway.change_terminaldevice")
def device_parameters(request, pk):
    """Edit the working parameters pushed to the device on its next heartbeat."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    form = TerminalParametersForm(request.POST or None, device=device)
    if request.method == "POST" and form.is_valid():
        changes = form.changed_parameters()
        if changes:
            device.work_parameters = {**device.work_parameters, **changes}
            device.params_dirty = True
            device.save(update_fields=["work_parameters", "params_dirty"])
            messages.success(
                request,
                _("%(count)d parameter(s) queued for the device.") % {"count": len(changes)},
            )
        else:
            messages.info(request, _("No parameter changed."))
        return redirect("terminal-device-parameters", pk=pk)
    readonly = {
        k: v
        for k, v in (device.work_parameters or {}).items()
        if k not in form.fields and k not in READ_ONLY_PARAMETERS
        and not isinstance(v, (dict, list))
    }
    return render(
        request,
        "device_gateway/device_parameters.html",
        {
            "device": device,
            "form": form,
            "grouped_fields": [
                (title, [form[name] for name in names]) for title, names in form.groups
            ],
            "other_parameters": sorted(readonly.items()),
            "no_parameters": not device.work_parameters,
        },
    )


@login_required
@permission_required("device_gateway.change_terminaldevice")
def employee_mapping_edit(request, pk, mapping_id):
    """Edit the access rules sent with an employee to this terminal."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    mapping = get_object_or_404(TerminalEmployee, pk=mapping_id, device=device)
    form = TerminalEmployeeAccessForm(request.POST or None, instance=mapping)
    if request.method == "POST" and form.is_valid():
        form.save()
        TerminalSyncTask.objects.get_or_create(
            device=device,
            employee=mapping.employee,
            action="add",
            status="pending",
            defaults={"device_user_id": mapping.device_user_id},
        )
        messages.success(request, _("Access rules saved and queued for the terminal."))
        return redirect("terminal-device-detail", pk=pk)
    return render(
        request,
        "device_gateway/mapping_form.html",
        {"device": device, "mapping": mapping, "form": form},
    )


@login_required
@permission_required("device_gateway.change_terminaldevice")
def employee_mapping_action(request, pk, mapping_id, action):
    """Queue a push (re-sync) or removal for one mapped employee."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    mapping = get_object_or_404(TerminalEmployee, pk=mapping_id, device=device)
    if request.method != "POST":
        return redirect("terminal-device-detail", pk=pk)
    if action == "sync":
        TerminalSyncTask.objects.get_or_create(
            device=device,
            employee=mapping.employee,
            action="add",
            status="pending",
            defaults={"device_user_id": mapping.device_user_id},
        )
        messages.success(request, _("Employee queued for the terminal."))
    elif action == "remove":
        TerminalSyncTask.objects.get_or_create(
            device=device,
            employee=mapping.employee,
            action="delete",
            status="pending",
            device_user_id=mapping.device_user_id,
        )
        messages.success(request, _("Removal from the terminal queued."))
    else:
        messages.error(request, _("Unknown action."))
    return redirect("terminal-device-detail", pk=pk)


@login_required
@permission_required("device_gateway.change_terminaldevice")
def roster_remove(request, pk, user_id):
    """Queue deletion of a device-side user that has no mapped employee."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    if request.method == "POST":
        TerminalSyncTask.objects.get_or_create(
            device=device,
            action="delete",
            status="pending",
            device_user_id=user_id,
        )
        messages.success(request, _("Removal from the terminal queued."))
    return redirect("terminal-device-detail", pk=pk)


@login_required
@permission_required("device_gateway.view_terminaldevice")
def device_activity(request, pk):
    """Commands, door/system events, snapshots and the on-device roster."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    category = request.GET.get("category")
    events = device.system_records.all()
    if category in ("1", "2"):
        events = events.filter(category=int(category))
    page = paginate(request, events)
    for event in page:
        event.name = system_event_name(event.category, event.record_type)
        event.is_alarm = (
            event.category == TerminalSystemRecord.CATEGORY_SYSTEM
            and event.record_type in ALARM_EVENT_TYPES
        )
    commands = list(device.commands.order_by("-id")[:20])
    for command in commands:
        ticket = command.payload.get("RegisterIdentifyTicket")
        outcome = command.result.get("Result") if isinstance(command.result, dict) else None
        command.result_label = (
            REGISTER_RESULTS.get(outcome, outcome) if ticket else ""
        )
    return render(
        request,
        "device_gateway/device_activity.html",
        {
            "device": device,
            "events": page,
            "selected_category": category or "",
            "commands": commands,
            "snapshots": device.snapshots.all()[:12],
            "roster": device.roster.all()[:200],
            "mapped_ids": set(
                device.terminal_employees.values_list("device_user_id", flat=True)
            ),
            "pd": request.GET.urlencode(),
        },
    )


@login_required
@permission_required("device_gateway.change_terminaldevice")
def command_cancel(request, pk, command_id):
    """Withdraw a command the device has not picked up yet."""
    device = get_object_or_404(TerminalDevice, pk=pk)
    command = get_object_or_404(TerminalCommand, pk=command_id, device=device)
    if request.method == "POST" and command.status == "pending":
        command.delete()
        messages.success(request, _("Pending command cancelled."))
    return redirect("terminal-device-activity", pk=pk)


@login_required
@permission_required("device_gateway.view_terminalpunchlog")
def punch_log_list(request):
    """Paginated punch log list with rich filters, search, and retry."""
    logs = (
        TerminalPunchLog.objects
        .select_related(
            "device",
            "employee",
            "employee__employee_work_info",
            "employee__employee_work_info__department_id",
        )
        .order_by("-record_date")
    )

    search_query = (request.GET.get("search") or request.GET.get("q") or "").strip()
    if search_query:
        logs = logs.filter(
            Q(employee__employee_first_name__icontains=search_query)
            | Q(employee__employee_last_name__icontains=search_query)
            | Q(employee__badge_id__icontains=search_query)
            | Q(user_id__icontains=search_query)
            | Q(record_id__icontains=search_query)
        )

    device_id = request.GET.get("device")
    if device_id:
        logs = logs.filter(device_id=device_id)

    record_type = request.GET.get("record_type")
    if record_type:
        try:
            logs = logs.filter(record_type=int(record_type))
        except (ValueError, TypeError):
            pass

    attendance_filter = request.GET.get("attendance")
    if attendance_filter == "created":
        logs = logs.filter(attendance_created=True)
    elif attendance_filter == "pending":
        logs = logs.filter(attendance_created=False)

    page_obj = paginate(request, logs)
    for punch in page_obj:
        decorate_punch(punch)

    context = {
        "logs": page_obj,
        "devices": TerminalDevice.objects.all(),
        "selected_device": device_id or "",
        "selected_record_type": record_type or "",
        "selected_attendance": attendance_filter or "",
        "search_query": search_query,
        "record_types": [
            (k, v["name"]) for k, v in RECORD_TYPE_METADATA.items()
        ],
        "pd": request.GET.urlencode(),
    }
    return render(request, "device_gateway/punch_logs.html", context)


@login_required
@permission_required("device_gateway.view_terminalsynctask")
def sync_task_list(request):
    """Paginated sync task list with search, filters, and batch retry."""
    tasks = (
        TerminalSyncTask.objects
        .select_related(
            "device",
            "employee",
            "employee__employee_work_info",
            "employee__employee_work_info__department_id",
        )
        .order_by("-created_at")
    )

    search_query = (request.GET.get("search") or request.GET.get("q") or "").strip()
    if search_query:
        tasks = tasks.filter(
            Q(employee__employee_first_name__icontains=search_query)
            | Q(employee__employee_last_name__icontains=search_query)
            | Q(employee__badge_id__icontains=search_query)
            | Q(device_user_id__icontains=search_query)
        )

    device_id = request.GET.get("device")
    if device_id:
        tasks = tasks.filter(device_id=device_id)

    status = request.GET.get("status")
    if status:
        tasks = tasks.filter(status=status)

    action = request.GET.get("action")
    if action:
        tasks = tasks.filter(action=action)

    failed_tasks_count = TerminalSyncTask.objects.filter(status="failed")
    if device_id:
        failed_tasks_count = failed_tasks_count.filter(device_id=device_id)
    failed_count = failed_tasks_count.count()

    context = {
        "tasks": paginate(request, tasks),
        "devices": TerminalDevice.objects.all(),
        "selected_device": device_id or "",
        "selected_status": status or "",
        "selected_action": action or "",
        "search_query": search_query,
        "status_choices": TerminalSyncTask.STATUS_CHOICES,
        "action_choices": TerminalSyncTask.ACTION_CHOICES,
        "failed_count": failed_count,
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
    referer = request.META.get("HTTP_REFERER")
    if referer:
        return redirect(referer)
    return redirect("terminal-sync-tasks")


@login_required
@permission_required("device_gateway.change_terminalsynctask")
def sync_task_retry_all(request):
    """Reset all failed sync tasks to pending (POST only)."""
    if request.method == "POST":
        qs = TerminalSyncTask.objects.filter(status="failed")
        device_id = request.POST.get("device")
        if device_id:
            qs = qs.filter(device_id=device_id)
        count = qs.update(status="pending")
        messages.success(
            request,
            _("Successfully re-queued %(count)d failed sync tasks.") % {"count": count},
        )
    referer = request.META.get("HTTP_REFERER")
    if referer:
        return redirect(referer)
    return redirect("terminal-sync-tasks")
