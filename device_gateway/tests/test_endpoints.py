import json
import time
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from employee.models import Employee
from horilla.testkit import make_employee as make_hr_employee, make_user
from base.models import Company

from ..models import (
    TerminalCommand,
    TerminalDevice,
    TerminalDevicePerson,
    TerminalEmployee,
    TerminalPunchLog,
    TerminalSyncTask,
    TerminalSystemRecord,
)
from ..forms import TerminalParametersForm

BASE = "/api/terminal"


def make_device(**kwargs):
    defaults = {"sn": "FC-8200HTEST01", "name": "Test Terminal",
                "company_id": Company.objects.get_or_create(company="Device tests")[0]}
    defaults.update(kwargs)
    return TerminalDevice.objects.create(**defaults)


def make_employee(email="dev.emp@test.com"):
    return make_hr_employee(
        company=Company.objects.get_or_create(company="Device tests")[0],
        first_name="Dev", last_name="Emp", email=email, phone="9999999999",
    )


class KeepaliveTests(TestCase):
    def test_unknown_sn_gets_401(self):
        resp = self.client.post(
            f"{BASE}/Device/Keepalive", {"SN": "NOPE"}, content_type="application/json"
        )
        self.assertEqual(resp.json()["Success"], 401)

    def test_registered_device_gets_flags(self):
        device = make_device()
        TerminalCommand.objects.unscoped().create(device=device, payload={"Opendoor": 1})
        TerminalSyncTask.objects.unscoped().create(
            device=device, device_user_id=1, action="delete"
        )
        resp = self.client.post(
            f"{BASE}/Device/Keepalive",
            {"SN": device.sn, "RelayStatus": 0, "AlarmStatus": ""},
            content_type="application/json",
        )
        body = resp.json()
        self.assertEqual(body["Success"], 1)
        self.assertEqual(body["Remote"], 1)
        self.assertEqual(body["DeletePeople"], 1)
        device.refresh_from_db()
        self.assertEqual(device.device_status["AlarmStatus"], "")
        self.assertIsNotNone(device.last_seen)

    def test_connect_password_enforced(self):
        device = make_device(connect_password="s3cret")
        resp = self.client.post(
            f"{BASE}/Device/Keepalive", {"SN": device.sn}, content_type="application/json"
        )
        self.assertEqual(resp.json()["Success"], 401)
        resp = self.client.post(
            f"{BASE}/Device/Keepalive",
            {"SN": device.sn, "ConnectPassword": "s3cret"},
            content_type="application/json",
        )
        self.assertEqual(resp.json()["Success"], 1)


class PunchRecordTests(TestCase):
    def _post_record(self, device, record_id=100, user_id="1", record_type=3):
        detail = {
            "RecordID": record_id,
            "RecordType": record_type,
            "RecordDate": int(time.time()),
            "UserID": user_id,
            "IsEntry": 1,
        }
        return self.client.post(
            f"{BASE}/Record/UploadIdentifyRecord",
            data={"SN": device.sn, "RecordDetail": json.dumps(detail)},
        )

    def test_punch_creates_attendance_and_dedupes(self):
        device = make_device()
        employee = make_employee()
        TerminalEmployee.objects.unscoped().create(
            device=device, employee=employee, device_user_id=1
        )
        resp = self._post_record(device)
        self.assertEqual(resp.json()["Success"], 1)
        self.assertEqual(TerminalPunchLog.objects.unscoped().count(), 1)
        log = TerminalPunchLog.objects.unscoped().get()
        self.assertTrue(log.attendance_created)

        # Same record again -> dedup, no second attendance row
        resp = self._post_record(device)
        self.assertEqual(resp.json()["Success"], 1)
        self.assertEqual(TerminalPunchLog.objects.unscoped().count(), 1)

    def test_unmapped_user_logged_not_attended(self):
        device = make_device()
        self._post_record(device, user_id="999")
        log = TerminalPunchLog.objects.unscoped().get()
        self.assertFalse(log.attendance_created)
        self.assertIsNone(log.employee)

    def test_saved_unmapped_punch_can_be_retried_after_mapping(self):
        from ..attendance_sync import process_punch

        device = make_device()
        employee = make_employee()
        self._post_record(device, record_id=101, user_id="7")
        log = TerminalPunchLog.objects.unscoped().get(device=device, record_id=101)
        self.assertFalse(log.attendance_created)
        TerminalEmployee.objects.unscoped().create(
            device=device, employee=employee, device_user_id=7
        )
        self.assertEqual(process_punch(device, log.raw, retry=True), "created")
        self.assertEqual(process_punch(device, log.raw, retry=True), "duplicate")
        log.refresh_from_db()
        self.assertTrue(log.attendance_created)

    def test_failed_verification_never_creates_attendance(self):
        device = make_device()
        employee = make_employee()
        TerminalEmployee.objects.unscoped().create(
            device=device, employee=employee, device_user_id=1
        )
        # RecordType 19 = Unregistered User, 24 = Blacklist
        self._post_record(device, record_id=200, record_type=19)
        self._post_record(device, record_id=201, record_type=24)
        self.assertFalse(
            TerminalPunchLog.objects.unscoped().filter(attendance_created=True).exists()
        )


class PeopleSyncTests(TestCase):
    def test_download_and_result_cycle(self):
        device = make_device()
        employee = make_employee()
        TerminalSyncTask.objects.unscoped().create(
            device=device, employee=employee, action="add"
        )
        resp = self.client.post(
            f"{BASE}/People/DownloadPeopleList",
            {"SN": device.sn, "Limit": 100},
            content_type="application/json",
        )
        body = resp.json()
        self.assertEqual(body["Success"], 1)
        self.assertEqual(body["PeopleCount"], 1)
        person = body["PeopleList"][0]
        self.assertIn(person["UserID"], (str(employee.pk),))
        self.assertTrue(
            TerminalEmployee.objects.unscoped().filter(
                device=device, employee=employee
            ).exists()
        )

        # Second call: nothing pending -> empty list stops the device cycle
        resp = self.client.post(
            f"{BASE}/People/DownloadPeopleList",
            {"SN": device.sn, "Limit": 100},
            content_type="application/json",
        )
        self.assertEqual(resp.json()["PeopleCount"], 0)

        # Ack with a failure report
        self.client.post(
            f"{BASE}/People/DownloadPeopleListResult",
            {"SN": device.sn, "SuccessCount": 1, "FailCount": 0, "FailList": []},
            content_type="application/json",
        )
        self.assertTrue(
            TerminalSyncTask.objects.unscoped().filter(device=device, status="acked").exists()
        )

    def test_delete_list(self):
        device = make_device()
        TerminalSyncTask.objects.unscoped().create(
            device=device, device_user_id=7, action="delete"
        )
        resp = self.client.post(
            f"{BASE}/People/DeletePeopleList",
            {"SN": device.sn},
            content_type="application/json",
        )
        body = resp.json()
        self.assertEqual(body["DeleteAll"], 0)
        self.assertEqual(body["DeleteList"], [7])

    def test_unacknowledged_people_batch_is_retried_after_timeout(self):
        device = make_device()
        task = TerminalSyncTask.objects.unscoped().create(
            device=device, employee=make_employee(), action="add"
        )
        url = f"{BASE}/People/DownloadPeopleList"
        payload = {"SN": device.sn, "Limit": 10}
        first = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(first.json()["PeopleCount"], 1)
        immediate = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(immediate.json()["PeopleCount"], 0)
        TerminalSyncTask.objects.unscoped().filter(pk=task.pk).update(
            sent_at=timezone.now() - timedelta(minutes=3)
        )
        retry = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(retry.json()["PeopleCount"], 1)


class RequestAuthorizationTests(TestCase):
    def test_known_user_opens_door(self):
        device = make_device()
        employee = make_employee()
        TerminalEmployee.objects.unscoped().create(
            device=device, employee=employee, device_user_id=5
        )
        detail = {
            "RecordID": 900,
            "RecordType": 3,
            "RecordDate": int(time.time()),
            "UserID": "5",
            "IsEntry": 1,
        }
        resp = self.client.post(
            f"{BASE}/Device/RequestAuthorization",
            data={"SN": device.sn, "RecordDetail": json.dumps(detail)},
        )
        self.assertEqual(resp.json()["Success"], 1)

    def test_unknown_user_refused(self):
        device = make_device()
        detail = {
            "RecordID": 901,
            "RecordType": 3,
            "RecordDate": int(time.time()),
            "UserID": "999",
            "IsEntry": 1,
        }
        resp = self.client.post(
            f"{BASE}/Device/RequestAuthorization",
            data={"SN": device.sn, "RecordDetail": json.dumps(detail)},
        )
        body = resp.json()
        self.assertEqual(body["Success"], 0)
        self.assertIn("Message", body)
        again = self.client.post(
            f"{BASE}/Device/RequestAuthorization",
            data={"SN": device.sn, "RecordDetail": json.dumps(detail)},
        )
        self.assertEqual(again.json()["Success"], 0)


def post(client, path, device, **data):
    return client.post(
        f"{BASE}/{path}", {"SN": device.sn, **data}, content_type="application/json"
    )


class DeviceManagementTests(TestCase):
    def test_empty_parameters_request_upload_not_sync(self):
        device = make_device()
        body = post(self.client, "Device/Keepalive", device).json()
        self.assertEqual(body["UploadWorkParameter"], 1)
        self.assertNotIn("SyncParameter", body)

    def test_parameter_edit_is_pushed_without_read_only_keys(self):
        device = make_device(
            work_parameters={"Volume": 3, "DeviceSN": device_sn(), "WiredMAC": "x"},
            params_dirty=True,
        )
        self.assertEqual(
            post(self.client, "Device/Keepalive", device).json()["SyncParameter"], 1
        )
        body = post(self.client, "Device/DownloadWorkSetting", device).json()
        self.assertEqual(body["Volume"], 3)
        self.assertIn("SystemTime", body)
        self.assertNotIn("WiredMAC", body)
        self.assertNotIn("DeviceSN", body)
        device.refresh_from_db()
        self.assertFalse(device.params_dirty)

    def test_upload_does_not_clobber_pending_edits(self):
        device = make_device(work_parameters={"Volume": 9}, params_dirty=True)
        post(
            self.client,
            "Device/UploadWorkSetting",
            device,
            Volume=1,
            FirmwareVerson="8.46",
        )
        device.refresh_from_db()
        self.assertEqual(device.work_parameters["Volume"], 9)
        self.assertEqual(device.work_parameters["FirmwareVerson"], "8.46")
        self.assertTrue(device.params_dirty)

    def test_refresh_request_flag_cleared_by_upload(self):
        device = make_device(
            work_parameters={"Volume": 1}, params_refresh_requested=True
        )
        self.assertEqual(
            post(self.client, "Device/Keepalive", device).json()["UploadWorkParameter"], 1
        )
        post(self.client, "Device/UploadWorkSetting", device, Volume=2)
        device.refresh_from_db()
        self.assertFalse(device.params_refresh_requested)
        self.assertIsNotNone(device.last_params_at)

    def test_delivered_command_released_after_heartbeats(self):
        device = make_device(work_parameters={"Volume": 1})
        command = TerminalCommand.objects.unscoped().create(
            device=device, payload={"Restart": 1}
        )
        body = post(self.client, "Device/RemoteCommand", device).json()
        self.assertEqual(body["Restart"], 1)
        TerminalCommand.objects.unscoped().filter(pk=command.pk).update(
            sent_at=timezone.now() - timedelta(minutes=2)
        )
        post(self.client, "Device/Keepalive", device)
        command.refresh_from_db()
        self.assertEqual(command.status, "acked")

    def test_registration_result_acks_only_registration_command(self):
        device = make_device(work_parameters={"Volume": 1})
        TerminalCommand.objects.unscoped().create(
            device=device, payload={"Restart": 1}, status="sent"
        )
        ticket = TerminalCommand.objects.unscoped().create(
            device=device,
            payload={"RegisterIdentifyTicket": {"RegisterType": 4, "UserID": 5}},
            status="sent",
        )
        resp = self.client.post(
            f"{BASE}/Device/RegisterIdentifyTicketResult",
            {
                "SN": device.sn,
                "ResultJson": json.dumps(
                    {
                        "SN": device.sn,
                        "UserID": 5,
                        "Result": 1,
                        "UserDetail": {"UserID": "5", "Name": "Jo", "CardNum": "77"},
                    }
                ),
            },
        )
        self.assertEqual(resp.json()["Success"], 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "acked")
        self.assertEqual(
            TerminalCommand.objects.unscoped().filter(status="sent").count(), 1
        )
        person = TerminalDevicePerson.objects.unscoped().get(device=device, user_id=5)
        self.assertEqual(person.card_num, "77")

    def test_push_people_builds_and_prunes_roster(self):
        device = make_device()
        detail = {"UserID": "12", "Name": "Ann", "Fingerprints": [{"Num": 1}]}
        for push_type in (1, 2):
            self.client.post(
                f"{BASE}/People/PushPeople",
                {"SN": device.sn, "PushType": push_type, "Detail": json.dumps(detail)},
            )
        roster = TerminalDevicePerson.objects.unscoped().filter(device=device)
        self.assertEqual(roster.count(), 1)
        self.assertEqual(roster.first().fingerprint_count, 1)
        self.client.post(
            f"{BASE}/People/PushPeople",
            {"SN": device.sn, "PushType": 3, "Detail": json.dumps({"UserID": "12"})},
        )
        self.assertFalse(roster.exists())

    def test_system_records_keep_category_apart(self):
        device = make_device()
        post(
            self.client,
            "Record/UploadSystemRecord",
            device,
            RecordType=1,
            Records=[{"RecordID": 1, "RecordType": 1, "RecordDate": 1718616771}],
        )
        post(
            self.client,
            "Record/UploadSystemRecord",
            device,
            RecordType=2,
            Records=[{"RecordID": 1, "RecordType": 28, "RecordDate": 1718616772}],
        )
        records = TerminalSystemRecord.objects.unscoped().filter(device=device)
        self.assertEqual(sorted(records.values_list("category", flat=True)), [1, 2])

    def test_person_payload_uses_access_rules_and_failure_is_explained(self):
        device = make_device()
        employee = make_employee()
        TerminalEmployee.objects.unscoped().create(
            device=device,
            employee=employee,
            device_user_id=44,
            card_num="1234",
            access_type=1,
            open_times=5,
            timegroup=3,
        )
        TerminalSyncTask.objects.unscoped().create(
            device=device, employee=employee, action="add"
        )
        person = post(
            self.client, "People/DownloadPeopleList", device, Limit=10
        ).json()["PeopleList"][0]
        self.assertEqual(person["UserID"], "44")
        self.assertEqual(person["CardNum"], "1234")
        self.assertEqual(person["AccessType"], 1)
        self.assertEqual(person["OpenTimes"], 5)
        self.assertEqual(person["Timegroup"], 3)
        post(
            self.client,
            "People/DownloadPeopleListResult",
            device,
            SuccessCount=0,
            FailCount=1,
            FailList=[{"UserID": "44", "ErrorCode": 30}],
        )
        task = TerminalSyncTask.objects.unscoped().get(device=device)
        self.assertEqual(task.status, "failed")
        self.assertIn("recognizable", task.fail_detail["ErrMsg"])


def device_sn():
    return "FC-8200HTEST01"


class ParametersFormTests(TestCase):
    def test_only_reported_keys_are_editable_and_changes_detected(self):
        device = make_device(work_parameters={"Volume": 3, "Voice": 1, "Language": 1})
        form = TerminalParametersForm(
            {"Volume": "5", "Voice": "on", "Language": "1"}, device=device
        )
        self.assertEqual(set(form.fields), {"Volume", "Voice", "Language"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.changed_parameters(), {"Volume": 5})

    def test_out_of_range_rejected(self):
        device = make_device(work_parameters={"Volume": 3})
        form = TerminalParametersForm({"Volume": "99"}, device=device)
        self.assertFalse(form.is_valid())


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class AdminUITests(TestCase):
    def setUp(self):
        from horilla.horilla_middlewares import set_selected_company

        self.device = make_device(work_parameters={"Volume": 3, "Voice": 1})
        set_selected_company(str(self.device.company_id_id))
        self.login("gw-admin", is_superuser=True)

    def login(self, username, **kwargs):
        user = make_user(username, **kwargs)
        make_hr_employee(
            company=self.device.company_id,
            email=f"{username}@test.horilla",
            user=user,
        )
        self.client.force_login(user)
        self.session_company()

    def session_company(self):
        session = self.client.session
        session["selected_company"] = str(self.device.company_id_id)
        session.save()

    def test_pages_render(self):
        employee = make_employee()
        mapping = TerminalEmployee.objects.unscoped().create(
            device=self.device, employee=employee, device_user_id=9
        )
        TerminalDevicePerson.objects.unscoped().create(
            device=self.device, user_id=77, name="Stranger"
        )
        urls = [
            reverse("terminal-device-detail", args=[self.device.pk]),
            reverse("terminal-device-parameters", args=[self.device.pk]),
            reverse("terminal-device-activity", args=[self.device.pk]),
            reverse("terminal-employee-edit", args=[self.device.pk, mapping.pk]),
        ]
        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, (url, resp.get("Location")))
        self.assertContains(
            self.client.get(urls[0] + "?uid=77"), "Stranger"
        )

    def test_actions_queue_commands(self):
        def act(name, **data):
            return self.client.post(
                reverse("terminal-device-action", args=[self.device.pk, name]), data
            )

        act("keep-open")
        act("register-credential", register_type="4", user_id="9")
        act("query-people", user_ids="1, 2")
        act("elevator", operation="lock", ports="1,2")
        act("elevator", operation="lock", ports="99")  # invalid, ignored
        act("push-firmware", url="ftp://bad", md5="x", version="1")  # invalid
        payloads = [
            c.payload for c in TerminalCommand.objects.unscoped().order_by("id")
        ]
        self.assertEqual(
            payloads,
            [
                {"Opendoor": 2},
                {"RegisterIdentifyTicket": {"RegisterType": 4, "UserID": 9}},
                {"QueryPeople": [1, 2]},
                {"LockElevatorPort": "1,2"},
            ],
        )

    def test_parameter_post_marks_dirty(self):
        self.client.post(
            reverse("terminal-device-parameters", args=[self.device.pk]),
            {"Volume": "7", "Voice": "on"},
        )
        self.device.refresh_from_db()
        self.assertEqual(self.device.work_parameters["Volume"], 7)
        self.assertTrue(self.device.params_dirty)

    def test_destructive_action_requires_delete_permission(self):
        self.login("gw-staff")
        self.client.post(
            reverse("terminal-device-action", args=[self.device.pk, "factory-reset"])
        )
        self.assertFalse(TerminalCommand.objects.unscoped().exists())
