"""
Device-facing endpoints of the FC terminal HTTP v2 docking protocol.

URL paths mirror the spec verbatim — including its typo
("UploadSnapshoot") — because the firmware constructs them literally.
"""

import base64
import hashlib
import logging
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .attendance_sync import process_punch
from django.core.files.base import ContentFile

from .models import (
    TerminalCommand,
    TerminalDevicePerson,
    TerminalEmployee,
    TerminalSnapshot,
    TerminalSyncTask,
    TerminalSystemRecord,
)
from .protocol import PERSON_ERROR_CODES, READ_ONLY_PARAMETERS
from .parsing import (
    authenticate_device,
    fail,
    get_photo_file,
    ok,
    parse_json_field,
)

logger = logging.getLogger(__name__)


def ready_sync_tasks(device, action):
    """Retry a sent batch only if its acknowledgement never arrived."""
    stale = timezone.now() - timedelta(minutes=2)
    return device.sync_tasks.filter(action__in=action).filter(
        Q(status="pending")
        | Q(status="sent", sent_at__isnull=True)
        | Q(status="sent", sent_at__lt=stale)
    )


@method_decorator(csrf_exempt, name="dispatch")
class BaseTerminalView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.device = authenticate_device(request)
        from horilla.horilla_middlewares import set_selected_company
        set_selected_company(str(self.device.company_id_id))


COMMAND_RELEASE_AFTER = timedelta(seconds=60)
REGISTRATION_TIMEOUT = timedelta(minutes=10)


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


class KeepaliveView(BaseTerminalView):
    """Device/Keepalive — heartbeat; response carries the work-order flags."""

    def post(self, request):
        device = self.device
        status_fields = {
            key: request.data.get(key)
            for key in ("RelayStatus", "KeepOpenStatus", "DoorSensorStatus",
                        "LockDoorStatus", "AlarmStatus")
            if request.data.get(key) is not None
        }
        if status_fields:
            device.device_status = status_fields
            device.save(update_fields=["device_status"])

        release_finished_commands(device)

        pending_people = ready_sync_tasks(device, ["add"]).exists()
        pending_deletes = ready_sync_tasks(device, ["delete", "delete_all"]).exists()
        payload = {}
        if pending_people:
            payload["AddPeople"] = 1
        if pending_deletes:
            payload["DeletePeople"] = 1
        if device.params_dirty:
            payload["SyncParameter"] = 1
        elif device.params_refresh_requested or not device.work_parameters:
            payload["UploadWorkParameter"] = 1
        if device.commands.filter(status="pending").exists():
            payload["Remote"] = 1
        return ok(**payload)


class UploadWorkSettingView(BaseTerminalView):
    """Device/UploadWorkSetting — device pushes its full parameter set."""

    def post(self, request):
        data = request.data
        if hasattr(data, "dict"):
            data = data.dict()
        reported = dict(data)
        if self.device.params_dirty:
            # Keep unsent edits; only refresh what the device alone knows.
            pending = {
                k: v
                for k, v in (self.device.work_parameters or {}).items()
                if k not in READ_ONLY_PARAMETERS
            }
            reported.update(pending)
        self.device.work_parameters = reported
        self.device.params_refresh_requested = False
        self.device.last_params_at = timezone.now()
        firmware = data.get("FirmwareVerson") or ""
        self.device.firmware_version = str(firmware)
        self.device.save(
            update_fields=[
                "work_parameters",
                "params_refresh_requested",
                "last_params_at",
                "firmware_version",
            ]
        )
        return ok()


class DownloadWorkSettingView(BaseTerminalView):
    """Device/DownloadWorkSetting — device pulls parameters we want set."""

    def post(self, request):
        params = {
            k: v
            for k, v in (self.device.work_parameters or {}).items()
            if k not in READ_ONLY_PARAMETERS
        }
        params["SystemTime"] = int(timezone.now().timestamp())
        self.device.params_dirty = False
        self.device.save(update_fields=["params_dirty"])
        return ok(**params)


class RemoteCommandView(BaseTerminalView):
    """Device/RemoteCommand — pop the oldest pending command."""

    def post(self, request):
        command = (
            self.device.commands.filter(status="pending").order_by("created_at").first()
        )
        if not command:
            return ok()
        command.status = "sent"
        command.sent_at = timezone.now()
        command.save(update_fields=["status", "sent_at"])
        return ok(**command.payload)


class RegisterIdentifyTicketResultView(BaseTerminalView):
    """Device/RegisterIdentifyTicketResult — registration outcome (+photo)."""

    def post(self, request):
        result = parse_json_field(request, "ResultJson") or dict(request.data)
        command = (
            self.device.commands.filter(
                status="sent", payload__has_key="RegisterIdentifyTicket"
            )
            .order_by("-created_at")
            .first()
        )
        if command:
            command.status = "acked"
            command.result = result
            command.save(update_fields=["status", "result"])
        photo = get_photo_file(request)
        detail = result.get("UserDetail") if isinstance(result, dict) else None
        if isinstance(detail, dict):
            upsert_roster_person(self.device, detail, photo)
        elif photo:
            TerminalSnapshot.objects.create(device=self.device, photo=photo)
        return ok()


class UploadSnapshootView(BaseTerminalView):
    """Device/UploadSnapshoot (sic — spec/firmware spelling)."""

    def post(self, request):
        photo = get_photo_file(request)
        if not photo:
            return fail("No photo")
        TerminalSnapshot.objects.create(device=self.device, photo=photo)
        return ok()


class RequestAuthorizationView(BaseTerminalView):
    """Device/RequestAuthorization — online check before opening the door."""

    def post(self, request):
        record = parse_json_field(request, "RecordDetail", "recordJson")
        if not record:
            return fail("Missing RecordDetail")
        outcome = process_punch(self.device, record, photo=get_photo_file(request))
        if outcome in ("created", "duplicate"):
            return ok()
        if outcome == "unmapped":
            return fail("Unregistered user")
        return fail("Verification not accepted")


class DownloadPeopleListView(BaseTerminalView):
    """People/DownloadPeopleList — batch of pending add/update personnel."""

    def post(self, request):
        try:
            limit = int(request.data.get("Limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 1000))
        tasks = (
            ready_sync_tasks(self.device, ["add"])
            .select_related(
                "employee",
                "employee__employee_work_info",
                "employee__employee_work_info__department_id",
                "employee__employee_work_info__job_position_id",
            )
            .order_by("created_at")[:limit]
        )
        people = []
        for task in tasks:
            person = self._person_json(task)
            if person:
                people.append(person)
                task.status = "sent"
                task.sent_at = timezone.now()
                task.save(update_fields=["status", "sent_at"])
        return ok(PeopleCount=len(people), PeopleList=people)

    def _person_json(self, task):
        employee = task.employee
        if not employee:
            return None
        work_info = getattr(employee, "employee_work_info", None)
        mapping = TerminalEmployee.objects.filter(
            device=self.device, employee=employee
        ).first()
        if mapping:
            device_user_id = mapping.device_user_id
        else:
            device_user_id = task.device_user_id or employee.pk
            mapping = TerminalEmployee.objects.create(
                device=self.device, employee=employee,
                device_user_id=device_user_id,
            )
        if task.device_user_id != device_user_id:
            task.device_user_id = device_user_id
            task.save(update_fields=["device_user_id"])
        photo = self._photo_b64(employee)
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
                int(mapping.expiration_date.timestamp())
                if mapping.expiration_date
                else 0
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

    def _photo_b64(self, employee):
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


class DownloadPeopleListResultView(BaseTerminalView):
    """People/DownloadPeopleListResult — import outcome per user."""

    def post(self, request):
        failed_pks = []
        for item in request.data.get("FailList") or []:
            task = (
                self.device.sync_tasks.filter(
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
        sent = self.device.sync_tasks.filter(status="sent", action="add")
        if failed_pks:
            sent = sent.exclude(pk__in=failed_pks)
        delivered_ids = list(sent.values_list("device_user_id", flat=True))
        TerminalEmployee.objects.filter(
            device=self.device, device_user_id__in=delivered_ids
        ).update(synced_at=timezone.now())
        sent.update(status="acked")
        return ok()


class DeletePeopleListView(BaseTerminalView):
    """People/DeletePeopleList — users to remove from the device."""

    def post(self, request):
        tasks = ready_sync_tasks(self.device, ["delete", "delete_all"])
        if tasks.filter(action="delete_all").exists():
            tasks.update(status="sent", sent_at=timezone.now())
            return ok(DeleteAll=1, DeleteCount=0, DeleteList=[])
        ids = [t.device_user_id for t in tasks if t.device_user_id is not None]
        tasks.update(status="sent", sent_at=timezone.now())
        return ok(DeleteAll=0, DeleteCount=len(ids), DeleteList=ids)


class DeletePeopleListResultView(BaseTerminalView):
    """People/DeletePeopleListResult — deletion outcome."""

    def post(self, request):
        deleted = set(request.data.get("DeleteList") or request.data.get("DeletList") or [])
        qs = self.device.sync_tasks.filter(status="sent").filter(
            action__in=["delete", "delete_all"]
        )
        if str(request.data.get("DeleteAll", "0")) == "1":
            qs.update(status="acked")
            TerminalEmployee.objects.filter(device=self.device).delete()
            TerminalDevicePerson.objects.filter(device=self.device).delete()
        else:
            qs.filter(device_user_id__in=deleted).update(status="acked")
            TerminalEmployee.objects.filter(
                device=self.device, device_user_id__in=deleted
            ).delete()
            TerminalDevicePerson.objects.filter(
                device=self.device, user_id__in=deleted
            ).delete()
        return ok()


class PushPeopleView(BaseTerminalView):
    """People/PushPeople — device-side personnel changes pushed upstream."""

    def post(self, request):
        detail = parse_json_field(request, "Detail") or {}
        push_type = str(request.data.get("PushType") or "")
        user_id = detail.get("UserID") or request.data.get("UserID")
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            uid = None
        if uid is None:
            return ok()
        if push_type == "3":  # deleted on the device
            TerminalEmployee.objects.filter(
                device=self.device, device_user_id=uid
            ).delete()
            TerminalDevicePerson.objects.filter(
                device=self.device, user_id=uid
            ).delete()
        elif detail:
            # Mappings to employees are only ever created server-side;
            # device-side users are recorded in the roster for review.
            detail.setdefault("UserID", uid)
            upsert_roster_person(self.device, detail, get_photo_file(request))
        return ok()


class UploadIdentifyRecordView(BaseTerminalView):
    """Record/UploadIdentifyRecord — punch-in record (the core endpoint)."""

    def post(self, request):
        record = parse_json_field(request, "RecordDetail", "recordJson")
        if not record:
            return fail("Missing RecordDetail")
        process_punch(self.device, record, photo=get_photo_file(request))
        return ok()


class UploadSystemRecordView(BaseTerminalView):
    """Record/UploadSystemRecord — batch door-sensor/system events."""

    def post(self, request):
        try:
            batch_category = int(request.data.get("RecordType") or 2)
        except (TypeError, ValueError):
            batch_category = 2
        for item in request.data.get("Records") or []:
            record_date = unix_to_datetime(item.get("RecordDate"))
            if item.get("RecordID") is None or record_date is None:
                continue
            TerminalSystemRecord.objects.get_or_create(
                device=self.device,
                category=batch_category,
                record_id=int(item["RecordID"]),
                defaults={
                    "record_type": int(item.get("RecordType") or 0),
                    "record_date": record_date,
                },
            )
        return ok()
