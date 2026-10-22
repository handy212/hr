import time
from unittest import mock

from django.test import TestCase

from ..models import (
    TerminalCommand,
    TerminalDevicePerson,
    TerminalEmployee,
    TerminalPunchLog,
    TerminalSyncTask,
)
from ..transports import device_context, mqtt, ws_json
from .test_endpoints import make_device, make_employee


class FrameTests(TestCase):
    def test_round_trip_plain_and_gzip_both_orders(self):
        for use_gzip in (False, True):
            for order in ("little", "big"):
                frame = mqtt.encode_frame(
                    "KeepAlive", {"RelayStatus": 0}, "7", b"IMG",
                    use_gzip=use_gzip, byteorder=order,
                )
                message, attachment = mqtt.decode_frame(frame)
                self.assertEqual(message["cmd"], "KeepAlive")
                self.assertEqual(message["CmdID"], "7")
                self.assertEqual(attachment, b"IMG")

    def test_garbage_rejected(self):
        with self.assertRaises(mqtt.FrameError):
            mqtt.decode_frame(b"\x01\x02\x03")


class MqttTransportTests(TestCase):
    def setUp(self):
        self.device = make_device(sn="MQ-1", protocol="mqtt")

    def handle(self, cmd, body=None, cmd_id="", attachment=b""):
        message = {"cmd": cmd, "CmdID": cmd_id, "body": body or {}}
        with device_context(self.device):
            return mqtt.handle_message(self.device, message, attachment)

    def test_punch_creates_attendance_and_acks(self):
        employee = make_employee()
        TerminalEmployee.objects.unscoped().create(
            device=self.device, employee=employee, device_user_id=5
        )
        record = {"RecordID": 9, "RecordType": 3, "RecordDate": int(time.time()),
                  "UserID": "5", "IsEntry": 1}
        replies = self.handle("UploadIdentifyRecord", record, "abc", b"\xff\xd8photo")
        self.assertEqual([r.cmd for r in replies], ["UploadIdentifyRecordACK"])
        self.assertEqual(replies[0].cmd_id, "abc")
        log = TerminalPunchLog.objects.unscoped().get()
        self.assertTrue(log.attendance_created)
        self.assertTrue(log.photo)
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_online)

    def test_offline_will_and_system_records(self):
        self.handle("KeepAlive", {"AlarmStatus": "fire"})
        self.device.refresh_from_db()
        self.assertEqual(self.device.active_alarms, ["fire"])
        self.handle("Will_DeviceOffline")
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_online)
        replies = self.handle(
            "UploadSystemRecord",
            {"RecordType": 1, "Records": [{"RecordID": 1, "RecordType": 1,
                                           "RecordDate": int(time.time())}]},
            "s1",
        )
        self.assertEqual(replies[0].cmd, "UploadSystemRecordACK")

    def test_upload_people_fills_roster(self):
        replies = self.handle(
            "UploadPeople",
            {"PushType": 1, "UserID": "3", "Detail": {"UserID": "3", "Name": "Ann"}},
            "u1",
        )
        self.assertEqual(replies[0].cmd, "UploadPeopleACK")
        self.assertEqual(TerminalDevicePerson.objects.unscoped().get().name, "Ann")

    def test_dispatcher_sends_commands_and_settles_acks(self):
        TerminalCommand.objects.unscoped().create(device=self.device, payload={"Opendoor": 1})
        TerminalCommand.objects.unscoped().create(device=self.device, payload={"Snapshoot": 1})
        self.device.work_parameters = {"a": 1}
        self.device.save()
        dispatcher = mqtt.Dispatcher()
        with device_context(self.device):
            out = dispatcher.collect(self.device)
        self.assertEqual([o.cmd for o in out], ["RemoteCommand"])
        command = TerminalCommand.objects.unscoped().get(payload={"Opendoor": 1})
        self.assertEqual(command.status, "sent")
        self.handle("RemoteCommandACK", cmd_id=out[0].cmd_id)
        command.refresh_from_db()
        self.assertEqual(command.status, "acked")
        with device_context(self.device):
            dispatcher.collect(self.device)
        snapshot = TerminalCommand.objects.unscoped().get(payload={"Snapshoot": 1})
        self.assertEqual(snapshot.result["Result"], "unsupported")

    def test_dispatcher_pushes_person_one_at_a_time_and_deletes(self):
        self.device.work_parameters = {"a": 1}
        self.device.save()
        for n in (1, 2):
            TerminalSyncTask.objects.unscoped().create(
                device=self.device, employee=make_employee(f"p{n}@test.com"), action="add"
            )
        dispatcher = mqtt.Dispatcher()
        with device_context(self.device):
            first = dispatcher.collect(self.device)
            second = dispatcher.collect(self.device)
        self.assertEqual([o.cmd for o in first], ["PushPeople"])
        self.assertEqual(second, [])  # first push not yet acknowledged
        self.handle("PushPeopleACK", {"SuccessCount": 1, "FailCount": 0})
        self.assertEqual(
            TerminalSyncTask.objects.unscoped().filter(status="acked").count(), 1
        )
        TerminalSyncTask.objects.unscoped().create(
            device=self.device, device_user_id=7, action="delete"
        )
        with device_context(self.device):
            out = dispatcher.collect(self.device)
        self.assertEqual({o.cmd for o in out}, {"PushPeople", "PushDeletePeople"})
        delete = next(o for o in out if o.cmd == "PushDeletePeople")
        self.assertEqual(delete.body["DeleteList"], [7])

    def test_parameter_push_and_read(self):
        dispatcher = mqtt.Dispatcher()
        with device_context(self.device):
            out = dispatcher.collect(self.device)
        self.assertEqual([o.cmd for o in out], ["ReadWorkSetting"])
        self.device.work_parameters = {"FireAlarm": 1}
        self.device.params_dirty = True
        self.device.save()
        with device_context(self.device):
            out = dispatcher.collect(self.device)
        self.assertEqual(out[0].cmd, "PushWorkSetting")
        self.assertEqual(out[0].body["FireAlarm"], 1)
        self.assertIn("SystemTime", out[0].body)


class WsTransportTests(TestCase):
    def setUp(self):
        self.device = make_device(sn="WS-1", protocol="ws_json")
        self.session = ws_json.WsSession()

    def register(self):
        return self.session.receive(
            {"cmd": "reg", "sn": "WS-1", "devinfo": {"modelname": "X", "firmware": "v1"}}
        )

    def test_reg_rejects_wrong_protocol_or_unknown(self):
        reply = ws_json.WsSession().receive({"cmd": "reg", "sn": "NOPE"})[0]
        self.assertFalse(reply["result"])
        make_device(sn="MQ-X", protocol="mqtt")
        reply = ws_json.WsSession().receive({"cmd": "reg", "sn": "MQ-X"})[0]
        self.assertFalse(reply["result"])
        self.assertTrue(self.register()[0]["result"])
        self.device.refresh_from_db()
        self.assertEqual(self.device.firmware_version, "v1")
        self.assertTrue(self.device.is_online)

    def test_unregistered_session_refuses_data(self):
        reply = ws_json.WsSession().receive({"cmd": "sendlog", "sn": "WS-1", "record": []})[0]
        self.assertFalse(reply["result"])

    def test_sendlog_creates_attendance_and_dedupes(self):
        employee = make_employee()
        TerminalEmployee.objects.unscoped().create(
            device=self.device, employee=employee, device_user_id=2
        )
        self.register()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        message = {"cmd": "sendlog", "sn": "WS-1", "count": 1, "logindex": 4,
                   "record": [{"enrollid": 2, "time": stamp, "mode": 1, "inout": 0, "event": 0}]}
        with device_context(self.device):
            reply = self.session.receive(message)[0]
            again = self.session.receive(message)[0]
        self.assertTrue(reply["result"])
        self.assertEqual(reply["access"], 1)
        self.assertEqual(reply["logindex"], 4)
        self.assertTrue(again["result"])
        self.assertEqual(TerminalPunchLog.objects.unscoped().count(), 1)
        self.assertEqual(TerminalPunchLog.objects.unscoped().get().record_type, 1)

    def test_door_event_is_ignored_but_acknowledged(self):
        self.register()
        reply = self.session.receive(
            {"cmd": "sendlog", "count": 1, "logindex": 1,
             "record": [{"enrollid": 0, "time": "2026-01-01 08:00:00", "mode": 0}]}
        )[0]
        self.assertTrue(reply["result"])
        self.assertEqual(reply["access"], 0)
        self.assertEqual(TerminalPunchLog.objects.unscoped().count(), 0)

    def test_senduser_updates_roster(self):
        self.register()
        self.session.receive({"cmd": "senduser", "enrollid": 8, "name": "Bo",
                              "backupnum": 11, "admin": 1, "record": 12345})
        person = TerminalDevicePerson.objects.unscoped().get(user_id=8)
        self.assertEqual((person.name, person.card_num, person.access_type), ("Bo", "12345", 1))

    def test_commands_are_pushed_serially(self):
        self.register()
        self.device.work_parameters = {"a": 1}
        TerminalCommand.objects.unscoped().create(device=self.device, payload={"Opendoor": 1})
        TerminalCommand.objects.unscoped().create(device=self.device, payload={"Restart": 1})
        with device_context(self.device):
            self.assertEqual(self.session.next_command(), {"cmd": "opendoor"})
            self.assertIsNone(self.session.next_command())  # still waiting
            self.session.receive({"ret": "opendoor", "result": True})
            self.assertEqual(self.session.next_command(), {"cmd": "reboot"})
        statuses = dict(
            TerminalCommand.objects.unscoped().values_list("payload__Opendoor", "status")
        )
        self.assertEqual(statuses[1], "acked")

    def test_person_add_sequence_and_delete(self):
        self.register()
        self.device.work_parameters = {"a": 1}
        task = TerminalSyncTask.objects.unscoped().create(
            device=self.device, employee=make_employee(), action="add"
        )
        with device_context(self.device):
            first = self.session.next_command()
            self.assertEqual(first["cmd"], "setusername")
            self.session.receive({"ret": "setusername", "result": True})
        task.refresh_from_db()
        self.assertEqual(task.status, "acked")
        TerminalSyncTask.objects.unscoped().create(
            device=self.device, device_user_id=4, action="delete"
        )
        with device_context(self.device):
            command = self.session.next_command()
            self.assertEqual(command["cmd"], "deleteuser")
            self.assertEqual(command["enrollid"], 4)
            self.session.receive({"ret": "deleteuser", "result": True})
        self.assertEqual(
            TerminalSyncTask.objects.unscoped().get(action="delete").status, "acked"
        )
