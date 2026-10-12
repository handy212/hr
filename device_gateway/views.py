"""
Device-facing endpoints of the FC terminal HTTP v2 docking protocol.

URL paths mirror the spec verbatim — including its typo
("UploadSnapshoot") — because the firmware constructs them literally.
"""

import base64
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .attendance_sync import process_punch, resolve_employee
from .models import (
    TerminalCommand,
    TerminalEmployee,
    TerminalSnapshot,
    TerminalSyncTask,
    TerminalSystemRecord,
)
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

        pending_people = ready_sync_tasks(device, ["add"]).exists()
        pending_deletes = ready_sync_tasks(device, ["delete", "delete_all"]).exists()
        payload = {}
        if pending_people:
            payload["AddPeople"] = 1
        if pending_deletes:
            payload["DeletePeople"] = 1
        if device.params_dirty or not device.work_parameters:
            payload["SyncParameter"] = 1
        if device.commands.filter(status="pending").exists():
            payload["Remote"] = 1
        return ok(**payload)


class UploadWorkSettingView(BaseTerminalView):
    """Device/UploadWorkSetting — device pushes its full parameter set."""

    def post(self, request):
        data = request.data
        if hasattr(data, "dict"):
            data = data.dict()
        self.device.work_parameters = dict(data)
        self.device.params_dirty = False
        firmware = data.get("FirmwareVerson") or ""
        self.device.firmware_version = str(firmware)
        self.device.save(
            update_fields=["work_parameters", "params_dirty", "firmware_version"]
        )
        return ok()


class DownloadWorkSettingView(BaseTerminalView):
    """Device/DownloadWorkSetting — device pulls parameters we want set."""

    def post(self, request):
        params = dict(self.device.work_parameters or {})
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
        command.save(update_fields=["status"])
        return ok(**command.payload)


class RegisterIdentifyTicketResultView(BaseTerminalView):
    """Device/RegisterIdentifyTicketResult — registration outcome (+photo)."""

    def post(self, request):
        result = parse_json_field(request, "ResultJson") or dict(request.data)
        command = (
            self.device.commands.filter(status="sent").order_by("-created_at").first()
        )
        if command:
            command.status = "acked"
            command.result = result
            command.save(update_fields=["status", "result"])
        photo = get_photo_file(request)
        if photo:
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
            TerminalEmployee.objects.create(
                device=self.device, employee=employee,
                device_user_id=device_user_id,
            )
        return {
            "UserID": str(device_user_id),
            "Name": (employee.employee_first_name or "")
            + " "
            + (employee.employee_last_name or ""),
            "Job": str(getattr(work_info, "job_position_id", "") or "")[:64],
            "Department": str(getattr(work_info, "department_id", "") or "")[:64],
            "IdentityCard": getattr(employee, "identifier", "") or "",
            "Photo": self._photo_b64(employee),
            "AccessType": 0,
            "ExpirationDate": 0,
            "OpenTimes": 65535,
        }

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
        for item in request.data.get("FailList") or []:
            task = (
                self.device.sync_tasks.filter(
                    device_user_id=item.get("UserID"), status="sent"
                )
                .order_by("-created_at")
                .first()
            )
            if task:
                task.status = "failed"
                task.fail_detail = item
                task.save(update_fields=["status", "fail_detail"])
        self.device.sync_tasks.filter(status="sent", action="add").update(
            status="acked"
        )
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
        else:
            qs.filter(device_user_id__in=deleted).update(status="acked")
            TerminalEmployee.objects.filter(
                device=self.device, device_user_id__in=deleted
            ).delete()
        return ok()


class PushPeopleView(BaseTerminalView):
    """People/PushPeople — device-side personnel changes pushed upstream."""

    def post(self, request):
        detail = parse_json_field(request, "Detail") or {}
        push_type = request.data.get("PushType")
        user_id = detail.get("UserID")
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            uid = None
        if push_type in ("3", 3) and uid is not None:  # delete
            TerminalEmployee.objects.filter(
                device=self.device, device_user_id=uid
            ).delete()
        # add/update/query: the mapping is only ever created server-side;
        # unknown device-side users are logged, not turned into employees.
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
        records = request.data.get("Records") or []
        created = 0
        for item in records:
            if item.get("RecordID") is None or item.get("RecordDate") is None:
                continue
            _, was_created = TerminalSystemRecord.objects.get_or_create(
                device=self.device,
                record_id=int(item["RecordID"]),
                defaults={
                    "record_type": int(item.get("RecordType") or 0),
                    "record_date": timezone.datetime.fromtimestamp(
                        int(item["RecordDate"]), tz=timezone.get_current_timezone()
                    ),
                },
            )
            created += was_created
        return ok()
