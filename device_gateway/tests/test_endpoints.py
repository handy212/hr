import json
import time
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from employee.models import Employee
from horilla.testkit import make_employee as make_hr_employee
from base.models import Company

from ..models import (
    TerminalCommand,
    TerminalDevice,
    TerminalEmployee,
    TerminalPunchLog,
    TerminalSyncTask,
)

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
