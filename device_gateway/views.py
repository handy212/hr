"""
Device-facing endpoints of the FC terminal HTTP v2 docking protocol.

URL paths mirror the spec verbatim — including its typo
("UploadSnapshoot") — because the firmware constructs them literally.
"""

import logging

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .attendance_sync import process_punch
from .models import TerminalSnapshot
from .parsing import (
    authenticate_device,
    fail,
    get_photo_file,
    ok,
    parse_json_field,
)
from .services import (
    apply_delete_result,
    apply_device_person_push,
    apply_people_result,
    build_person_json,
    claim_delete_batch,
    mark_task_sent,
    pop_next_command,
    ready_sync_tasks,
    record_system_events,
    release_finished_commands,
    save_keepalive_status,
    save_work_settings,
    settings_to_push,
    upsert_roster_person,
)

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class BaseTerminalView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.device = authenticate_device(request)
        from horilla.horilla_middlewares import set_selected_company
        set_selected_company(str(self.device.company_id_id))


class KeepaliveView(BaseTerminalView):
    """Device/Keepalive — heartbeat; response carries the work-order flags."""

    def post(self, request):
        device = self.device
        save_keepalive_status(device, request.data)

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
        save_work_settings(self.device, data)
        return ok()


class DownloadWorkSettingView(BaseTerminalView):
    """Device/DownloadWorkSetting — device pulls parameters we want set."""

    def post(self, request):
        return ok(**settings_to_push(self.device))


class RemoteCommandView(BaseTerminalView):
    """Device/RemoteCommand — pop the oldest pending command."""

    def post(self, request):
        command = pop_next_command(self.device)
        return ok(**command.payload) if command else ok()


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
            person = build_person_json(self.device, task)
            if person:
                people.append(person)
                mark_task_sent(task)
        return ok(PeopleCount=len(people), PeopleList=people)


class DownloadPeopleListResultView(BaseTerminalView):
    """People/DownloadPeopleListResult — import outcome per user."""

    def post(self, request):
        apply_people_result(self.device, request.data.get("FailList"))
        return ok()


class DeletePeopleListView(BaseTerminalView):
    """People/DeletePeopleList — users to remove from the device."""

    def post(self, request):
        delete_all, ids = claim_delete_batch(self.device)
        if delete_all:
            return ok(DeleteAll=1, DeleteCount=0, DeleteList=[])
        return ok(DeleteAll=0, DeleteCount=len(ids), DeleteList=ids)


class DeletePeopleListResultView(BaseTerminalView):
    """People/DeletePeopleListResult — deletion outcome."""

    def post(self, request):
        deleted = request.data.get("DeleteList") or request.data.get("DeletList")
        apply_delete_result(
            self.device,
            str(request.data.get("DeleteAll", "0")) == "1",
            deleted,
        )
        return ok()


class PushPeopleView(BaseTerminalView):
    """People/PushPeople — device-side personnel changes pushed upstream."""

    def post(self, request):
        detail = parse_json_field(request, "Detail") or {}
        user_id = detail.get("UserID") or request.data.get("UserID")
        apply_device_person_push(
            self.device,
            request.data.get("PushType"),
            detail,
            user_id,
            get_photo_file(request),
        )
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
        record_system_events(self.device, batch_category, request.data.get("Records"))
        return ok()
