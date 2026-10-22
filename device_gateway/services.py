"""
Transport-independent device operations.

The HTTP v2, MQTT and WebSocket adapters all feed the same models; the
logic that decides *what to store and what to send* lives here so a fix in
one place applies to every protocol.
"""

import base64
import hashlib
import logging
from datetime import datetime, timedelta

from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone

from .models import (
    TerminalDevice,
    TerminalDevicePerson,
    TerminalEmployee,
    TerminalSystemRecord,
)
from .protocol import PERSON_ERROR_CODES, READ_ONLY_PARAMETERS

logger = logging.getLogger(__name__)

COMMAND_RELEASE_AFTER = timedelta(seconds=60)
REGISTRATION_TIMEOUT = timedelta(minutes=10)
SYNC_RETRY_AFTER = timedelta(minutes=2)
KEEPALIVE_STATUS_KEYS = (
    "RelayStatus",
    "KeepOpenStatus",
    "DoorSensorStatus",
    "LockDoorStatus",
    "AlarmStatus",
)


def ready_sync_tasks(device, action):
    """Retry a sent batch only if its acknowledgement never arrived."""
    stale = timezone.now() - SYNC_RETRY_AFTER
    return device.sync_tasks.filter(action__in=action).filter(
        Q(status="pending")
        | Q(status="sent", sent_at__isnull=True)
        | Q(status="sent", sent_at__lt=stale)
    )


def inflight_sync_tasks(device, action):
    """Sent tasks still waiting for their acknowledgement."""
    stale = timezone.now() - SYNC_RETRY_AFTER
    return device.sync_tasks.filter(
        action__in=action, status="sent", sent_at__gte=stale
    )


def release_finished_commands(device):
    """
    Most remote commands have no completion callback. Once the device keeps
    heartbeating after receiving one, it has run it. Registration commands
    wait for their result callback, with a generous timeout.
    """
    now = timezone.now()
    for command in device.commands.filter(status="sent"):
        waited = now - (command.sent_at or command.created_at)
        if "RegisterIdentifyTicket" in command.payload:
            if waited > REGISTRATION_TIMEOUT:
                command.status = "acked"
                command.result = {"Result": "timeout"}
                command.save(update_fields=["status", "result"])
        elif waited > COMMAND_RELEASE_AFTER:
            command.status = "acked"
            command.save(update_fields=["status"])


def unix_to_datetime(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.get_current_timezone())
    except (OverflowError, OSError, ValueError):
        return None


def upsert_roster_person(device, detail, photo=None):
    """Store/refresh a device-side personnel record without keeping blobs."""
    try:
        user_id = int(detail.get("UserID"))
    except (TypeError, ValueError):
        return None
    inline_photo = detail.get("Photo") or ""
    values = {
        "name": str(detail.get("Name") or "")[:100],
        "job": str(detail.get("Job") or "")[:100],
        "department": str(detail.get("Department") or "")[:100],
        "identity_card": str(detail.get("IdentityCard") or "")[:64],
        "card_num": str(detail.get("CardNum") or "")[:24],
        "access_type": int(detail.get("AccessType") or 0),
        "expiration_date": unix_to_datetime(detail.get("ExpirationDate")),
        "open_times": int(detail.get("OpenTimes", 65535) or 0),
        "has_face": bool(detail.get("FaceFeature")),
        "has_photo": bool(photo or inline_photo),
        "fingerprint_count": len(detail.get("Fingerprints") or []),
        "palmvein_count": len(detail.get("Palmveins") or []),
        "last_pushed_at": timezone.now(),
    }
    person, _ = TerminalDevicePerson.objects.update_or_create(
        device=device, user_id=user_id, defaults=values
    )
    if photo:
        person.photo.save(f"{device.pk}_{user_id}.jpg", photo, save=True)
    elif inline_photo and not str(inline_photo).startswith("http"):
        try:
            person.photo.save(
                f"{device.pk}_{user_id}.jpg",
                ContentFile(base64.b64decode(inline_photo)),
                save=True,
            )
        except (ValueError, TypeError):
            logger.warning("device_gateway: bad inline photo for user %s", user_id)
    return person


def save_keepalive_status(device, data):
    fields = {k: data.get(k) for k in KEEPALIVE_STATUS_KEYS if data.get(k) is not None}
    if fields:
        device.device_status = fields
        device.save(update_fields=["device_status"])


def save_work_settings(device, data):
    """Store the parameter set a device uploaded, keeping unsent edits."""
    reported = dict(data)
    if device.params_dirty:
        pending = {
            k: v
            for k, v in (device.work_parameters or {}).items()
            if k not in READ_ONLY_PARAMETERS
        }
        reported.update(pending)
    device.work_parameters = reported
    device.params_refresh_requested = False
    device.last_params_at = timezone.now()
    device.firmware_version = str(data.get("FirmwareVerson") or "")
    device.save(
        update_fields=[
            "work_parameters",
            "params_refresh_requested",
            "last_params_at",
            "firmware_version",
        ]
    )


def settings_to_push(device):
    """Writable parameters plus a fresh clock; clears the dirty flag."""
    params = {
        k: v
        for k, v in (device.work_parameters or {}).items()
        if k not in READ_ONLY_PARAMETERS
    }
    params["SystemTime"] = int(timezone.now().timestamp())
    device.params_dirty = False
    device.save(update_fields=["params_dirty"])
    return params


def pop_next_command(device):
    command = device.commands.filter(status="pending").order_by("created_at").first()
    if not command:
        return None
    command.status = "sent"
    command.sent_at = timezone.now()
    command.save(update_fields=["status", "sent_at"])
    return command


def _employee_photo_b64(employee):
    """Inline base64 rather than a URL: protected media URLs need a
    session the device doesn't have."""
    avatar = getattr(employee, "employee_profile", None)
    try:
        if avatar and avatar.size <= 400 * 1024:
            with avatar.open("rb") as f:
                blob = f.read()
            if blob:
                return base64.b64encode(blob).decode()
    except (OSError, ValueError):
        pass
    return ""


def build_person_json(device, task):
    """Personnel record for a pending 'add' task (HTTP v2 field names).

    ``Photo`` is inline base64; transports that ship photos as binary
    attachments decode it again.
    """
    employee = task.employee
    if not employee:
        return None
    work_info = getattr(employee, "employee_work_info", None)
    mapping = TerminalEmployee.objects.filter(device=device, employee=employee).first()
    if mapping:
        device_user_id = mapping.device_user_id
    else:
        device_user_id = task.device_user_id or employee.pk
        mapping = TerminalEmployee.objects.create(
            device=device, employee=employee, device_user_id=device_user_id
        )
    if task.device_user_id != device_user_id:
        task.device_user_id = device_user_id
        task.save(update_fields=["device_user_id"])
    photo = _employee_photo_b64(employee)
    person = {
        "UserID": str(device_user_id),
        "Name": (employee.employee_first_name or "")
        + " "
        + (employee.employee_last_name or ""),
        "Job": str(getattr(work_info, "job_position_id", "") or "")[:64],
        "Department": str(getattr(work_info, "department_id", "") or "")[:64],
        "IdentityCard": getattr(employee, "identifier", "") or "",
        "Photo": photo,
        "AccessType": mapping.access_type,
        "ExpirationDate": (
            int(mapping.expiration_date.timestamp()) if mapping.expiration_date else 0
        ),
        "OpenTimes": mapping.open_times,
    }
    if photo:
        raw = base64.b64decode(photo)
        person["PhotoMD5"] = hashlib.md5(raw).hexdigest().upper()
        person["PhotoLen"] = len(raw)
    if mapping.card_num:
        person["CardNum"] = mapping.card_num
    if mapping.timegroup:
        person["Timegroup"] = mapping.timegroup
    return person


def mark_task_sent(task):
    task.status = "sent"
    task.sent_at = timezone.now()
    task.save(update_fields=["status", "sent_at"])


def apply_people_result(device, fail_list):
    """Settle 'add' tasks after the device reports its import result."""
    failed_pks = []
    for item in fail_list or []:
        task = (
            device.sync_tasks.filter(
                device_user_id=item.get("UserID"), status="sent", action="add"
            )
            .order_by("-created_at")
            .first()
        )
        if task:
            detail = dict(item)
            if not detail.get("ErrMsg"):
                try:
                    code = int(detail.get("ErrorCode"))
                except (TypeError, ValueError):
                    code = None
                if code in PERSON_ERROR_CODES:
                    detail["ErrMsg"] = str(PERSON_ERROR_CODES[code])
            task.status = "failed"
            task.fail_detail = detail
            task.save(update_fields=["status", "fail_detail"])
            failed_pks.append(task.pk)
    sent = device.sync_tasks.filter(status="sent", action="add")
    if failed_pks:
        sent = sent.exclude(pk__in=failed_pks)
    delivered_ids = list(sent.values_list("device_user_id", flat=True))
    TerminalEmployee.objects.filter(
        device=device, device_user_id__in=delivered_ids
    ).update(synced_at=timezone.now())
    sent.update(status="acked")


def claim_delete_batch(device):
    """Mark pending deletions as sent; returns (delete_all, user_ids)."""
    tasks = ready_sync_tasks(device, ["delete", "delete_all"])
    if tasks.filter(action="delete_all").exists():
        tasks.update(status="sent", sent_at=timezone.now())
        return True, []
    ids = [t.device_user_id for t in tasks if t.device_user_id is not None]
    tasks.update(status="sent", sent_at=timezone.now())
    return False, ids


def apply_delete_result(device, delete_all, deleted_ids):
    qs = device.sync_tasks.filter(status="sent", action__in=["delete", "delete_all"])
    if delete_all:
        qs.update(status="acked")
        TerminalEmployee.objects.filter(device=device).delete()
        TerminalDevicePerson.objects.filter(device=device).delete()
    else:
        deleted = set(deleted_ids or [])
        qs.filter(device_user_id__in=deleted).update(status="acked")
        TerminalEmployee.objects.filter(
            device=device, device_user_id__in=deleted
        ).delete()
        TerminalDevicePerson.objects.filter(
            device=device, user_id__in=deleted
        ).delete()


def apply_device_person_push(device, push_type, detail, user_id, photo=None):
    """Device-side personnel change reported upstream.

    Mappings to employees are only ever created server-side; device-side
    users are recorded in the roster for review.
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return
    if str(push_type) == "3":  # deleted on the device
        TerminalEmployee.objects.filter(device=device, device_user_id=uid).delete()
        TerminalDevicePerson.objects.filter(device=device, user_id=uid).delete()
    elif detail:
        detail = dict(detail)
        detail.setdefault("UserID", uid)
        upsert_roster_person(device, detail, photo)


def record_system_events(device, category, records):
    for item in records or []:
        record_date = unix_to_datetime(item.get("RecordDate"))
        if item.get("RecordID") is None or record_date is None:
            continue
        TerminalSystemRecord.objects.get_or_create(
            device=device,
            category=category,
            record_id=int(item["RecordID"]),
            defaults={
                "record_type": int(item.get("RecordType") or 0),
                "record_date": record_date,
            },
        )


def find_gateway_device(sn, protocol):
    """Resolve a device that identified itself by SN on a push transport.

    Unlike the HTTP views there is no ConnectPassword to check, so the SN
    must belong to an active device registered for exactly this protocol.
    """
    from saas.features import company_has_feature

    device = TerminalDevice.all_objects.filter(sn=str(sn or "").strip()).first()
    if (
        not device
        or not device.is_active
        or device.protocol != protocol
        or not device.company_id_id
        or not company_has_feature(device.company_id_id, "devices")
    ):
        return None
    return device
