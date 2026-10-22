"""
WebSocket + JSON transport (cloud-solution protocol v2.4, SN/enrollid based).

The terminal opens the connection and registers with ``reg``; the server
answers each message and may push commands that the terminal answers with
``ret``. ``WsSession`` holds one connection's state and has no dependency on
a WebSocket library: ``run_ws_gateway`` feeds it text frames and sends what
it returns.
"""

import base64
import hashlib
import logging
import time
from datetime import datetime

from django.core.files.base import ContentFile
from django.utils import timezone

from .. import services
from ..attendance_sync import process_punch
from ..models import TerminalDevicePerson

logger = logging.getLogger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ANSWER_TIMEOUT = 20  # seconds to wait for a terminal's ``ret``

# ``mode`` of a log record -> RecordType understood by process_punch.
# The vendor document is inconsistent about which of 0/1 is fingerprint; the
# values used by the revision history (0 fp, 1 card, 2 password) are taken.
MODE_TO_RECORD_TYPE = {0: 2, 1: 1, 2: 10, 3: 3, 8: 3}
QR_VERIFY_MODE = 13
QR_RECORD_TYPE = 32

BACKUP_FINGERPRINT = range(0, 10)
BACKUP_PASSWORD = 10
BACKUP_CARD = 11
BACKUP_FACE = range(20, 28)
BACKUP_PALM = range(30, 38)
BACKUP_PHOTO = 50


def cloud_time():
    return timezone.localtime().strftime(TIME_FORMAT)


def _parse_time(value):
    try:
        naive = datetime.strptime(str(value).strip(), TIME_FORMAT)
    except ValueError:
        return None
    return timezone.make_aware(naive, timezone.get_current_timezone())


def record_to_punch(record):
    """Normalise a log record into the HTTP-v2 ``RecordDetail`` shape.

    The terminal sends no record number, so one is derived from the user and
    timestamp: retransmissions then land on the same ``TerminalPunchLog``.
    """
    when = _parse_time(record.get("time"))
    try:
        enroll_id = int(record.get("enrollid"))
    except (TypeError, ValueError):
        return None
    if when is None or enroll_id == 0:  # 0 = door event, not a person
        return None
    if record.get("verifymode") == QR_VERIFY_MODE:
        record_type = QR_RECORD_TYPE
    else:
        try:
            record_type = MODE_TO_RECORD_TYPE.get(int(record.get("mode", 3)), 3)
        except (TypeError, ValueError):
            record_type = 3
    digest = hashlib.sha1(f"{enroll_id}|{when.isoformat()}".encode()).hexdigest()
    try:
        is_entry = 0 if int(record.get("inout", 0)) == 1 else 1
    except (TypeError, ValueError):
        is_entry = 1
    return {
        "RecordID": int(digest[:12], 16),
        "RecordType": record_type,
        "RecordDate": int(when.timestamp()),
        "UserID": str(enroll_id),
        "IsEntry": is_entry,
        "BodyTemp": record.get("temp"),
    }


def _photo(record):
    image = record.get("image")
    if not image:
        return None
    try:
        return ContentFile(base64.b64decode(image), name="punch.jpg")
    except (ValueError, TypeError):
        return None


def _ingest_log(device, record):
    punch = record_to_punch(record)
    if punch is None:
        return "ignored"
    return process_punch(device, punch, photo=_photo(record))


class WsSession:
    """State of one terminal connection."""

    def __init__(self):
        self.device = None
        self.queue = []  # (message, callback(ok, reply) or None)
        self.awaiting = None  # (ret name, callback, deadline)

    @property
    def registered(self):
        return self.device is not None

    def close(self):
        if self.device is not None:
            self.device.is_online = False
            self.device.save(update_fields=["is_online"])

    def receive(self, message):
        """Handle one inbound JSON message; returns reply dicts."""
        cmd = message.get("cmd")
        if cmd == "reg":
            return [self._register(message)]
        if not self.registered:
            return [{"ret": cmd or "error", "result": False, "reason": "did not reg"}]
        self._touch()
        if cmd == "sendlog":
            return [self._send_log(message)]
        if cmd == "senduser":
            return [self._send_user(message)]
        if cmd == "sendqrcode":
            return [
                {
                    "ret": "sendqrcode",
                    "result": True,
                    "access": 0,
                    "enrollid": 0,
                    "username": "",
                    "message": "unsupported",
                }
            ]
        if message.get("ret"):
            return self._answer(message)
        logger.info("device_gateway: ignoring WS command %r", cmd)
        return []

    def _touch(self):
        self.device.last_seen = timezone.now()
        self.device.is_online = True
        self.device.save(update_fields=["last_seen", "is_online"])

    def _register(self, message):
        device = services.find_gateway_device(message.get("sn"), "ws_json")
        if device is None:
            return {"ret": "reg", "result": False, "reason": "device not activated"}
        self.device = device
        info = message.get("devinfo") or {}
        if info:
            services.save_work_settings(
                device,
                {
                    **(device.work_parameters or {}),
                    "ModelName": info.get("modelname", ""),
                    "FirmwareVerson": info.get("firmware", ""),
                    "MAC": info.get("mac", ""),
                    "Capacity": {
                        key: info.get(key)
                        for key in ("usersize", "facesize", "fpsize", "cardsize", "logsize")
                        if info.get(key) is not None
                    },
                },
            )
        self._touch()
        return {
            "ret": "reg",
            "result": True,
            "cloudtime": cloud_time(),
            "nosenduser": True,
        }

    def _send_log(self, message):
        records = message.get("record") or []
        access = 1
        for record in records:
            outcome = _ingest_log(self.device, record)
            if outcome == "error":
                return {"ret": "sendlog", "result": False, "reason": 1}
            if outcome not in ("created", "duplicate"):
                access = 0
        return {
            "ret": "sendlog",
            "result": True,
            "count": message.get("count", len(records)),
            "logindex": message.get("logindex", 0),
            "cloudtime": cloud_time(),
            "access": access,
            "message": "",
        }

    def _send_user(self, message):
        self._record_roster(message)
        return {"ret": "senduser", "result": True, "cloudtime": cloud_time()}

    def _record_roster(self, entry):
        try:
            user_id = int(entry.get("enrollid"))
            backup = int(entry.get("backupnum", -1))
        except (TypeError, ValueError):
            return
        person, _ = TerminalDevicePerson.objects.get_or_create(
            device=self.device, user_id=user_id
        )
        if entry.get("name"):
            person.name = str(entry["name"])[:100]
        person.access_type = 1 if entry.get("admin") else 0
        if backup == BACKUP_CARD:
            person.card_num = str(entry.get("record") or "")[:24]
        elif backup in BACKUP_FINGERPRINT:
            person.fingerprint_count = max(person.fingerprint_count, backup + 1)
        elif backup in BACKUP_FACE:
            person.has_face = True
        elif backup in BACKUP_PALM:
            person.palmvein_count = max(person.palmvein_count, 1)
        elif backup == BACKUP_PHOTO:
            person.has_photo = True
        person.last_pushed_at = timezone.now()
        person.save()

    def _answer(self, message):
        ret = message.get("ret")
        ok = bool(message.get("result"))
        if ret in ("getuserlist", "getnewlog", "getalllog") and ok:
            return self._paged(message, ret)
        if self.awaiting and self.awaiting[0] == ret:
            _, callback, _ = self.awaiting
            self.awaiting = None
            if callback:
                callback(ok, message)
        return []

    def _paged(self, message, ret):
        records = message.get("record") or []
        for record in records:
            if ret == "getuserlist":
                self._record_roster(record)
            else:
                _ingest_log(self.device, record)
        if records:
            if self.awaiting and self.awaiting[0] == ret:
                self.awaiting = (ret, self.awaiting[1], time.monotonic() + ANSWER_TIMEOUT)
            # The terminal sends the next package only after this request.
            return [{"cmd": ret, "stn": False}]
        if self.awaiting and self.awaiting[0] == ret:
            _, callback, _ = self.awaiting
            self.awaiting = None
            if callback:
                callback(True, message)
        return []

    def next_command(self):
        """Next command to push, or None while one is still outstanding."""
        if self.awaiting:
            if time.monotonic() < self.awaiting[2]:
                return None
            _, callback, _ = self.awaiting
            self.awaiting = None
            if callback:
                callback(False, {"reason": "timeout"})
        if not self.queue:
            self._collect()
        if not self.queue:
            return None
        message, callback = self.queue.pop(0)
        if message["cmd"] == "reboot":  # the terminal never answers
            if callback:
                callback(True, {})
            return message
        self.awaiting = (message["cmd"], callback, time.monotonic() + ANSWER_TIMEOUT)
        return message

    def _enqueue(self, message, callback=None):
        self.queue.append((message, callback))

    def _collect(self):
        device = self.device
        if device.params_dirty:
            services.settings_to_push(device)
            self._enqueue({"cmd": "settime", "cloudtime": cloud_time()})
            return

        services.release_finished_commands(device)
        if not device.commands.filter(status="sent").exists():
            command = services.pop_next_command(device)
            if command:
                self._queue_remote_command(command)
                return

        if not services.inflight_sync_tasks(device, ["add"]).exists():
            task = (
                services.ready_sync_tasks(device, ["add"])
                .select_related("employee")
                .order_by("created_at")
                .first()
            )
            if task:
                self._queue_person(task)
                return

        delete_actions = ["delete", "delete_all"]
        if (
            not services.inflight_sync_tasks(device, delete_actions).exists()
            and services.ready_sync_tasks(device, delete_actions).exists()
        ):
            self._queue_deletes()

    def _queue_remote_command(self, command):
        payload = command.payload
        message = None
        if payload.get("Opendoor") == 1:
            message = {"cmd": "opendoor"}
        elif payload.get("Restart"):
            message = {"cmd": "reboot"}
        elif payload.get("Recover"):
            message = {"cmd": "initsys"}
        elif payload.get("ClearRecord"):
            message = {"cmd": "cleanlog"}
        elif payload.get("PushAllPeople"):
            message = {"cmd": "getuserlist", "stn": True}
        elif payload.get("RepostRecord"):
            message = {"cmd": "getalllog", "stn": True}

        if message is None:
            command.status = "acked"
            command.result = {
                "Result": "unsupported",
                "Message": "Not available on WebSocket terminals",
            }
            command.save(update_fields=["status", "result"])
            return

        def done(ok, reply):
            command.status = "acked"
            command.result = {"Result": "ok" if ok else "failed", **(
                {} if ok else {"Message": str(reply.get("reason", ""))}
            )}
            command.save(update_fields=["status", "result"])

        self._enqueue(message, done)

    def _queue_person(self, task):
        device = self.device
        person = services.build_person_json(device, task)
        if not person:
            task.status = "failed"
            task.fail_detail = {"ErrMsg": "Employee no longer exists"}
            task.save(update_fields=["status", "fail_detail"])
            return
        services.mark_task_sent(task)
        user_id = int(person["UserID"])
        messages = [
            {
                "cmd": "setusername",
                "count": 1,
                "record": [{"enrollid": user_id, "name": person["Name"].strip()}],
            }
        ]
        if person.get("CardNum", "").isdigit():
            messages.append(self._user_info(user_id, person, BACKUP_CARD, int(person["CardNum"])))
        if person.get("Photo"):
            messages.append(self._user_info(user_id, person, BACKUP_PHOTO, person["Photo"]))

        state = {"left": len(messages), "failure": None}

        def done(ok, reply):
            state["left"] -= 1
            if not ok and not state["failure"]:
                state["failure"] = str(reply.get("reason", "rejected"))
            if state["left"] == 0:
                fails = (
                    [{"UserID": user_id, "ErrorCode": 1, "ErrMsg": state["failure"]}]
                    if state["failure"]
                    else []
                )
                services.apply_people_result(device, fails)

        for message in messages:
            self._enqueue(message, done)

    @staticmethod
    def _user_info(user_id, person, backupnum, record):
        return {
            "cmd": "setuserinfo",
            "enrollid": user_id,
            "name": person["Name"].strip(),
            "backupnum": backupnum,
            "admin": 1 if person.get("AccessType") == 1 else 0,
            "record": record,
        }

    def _queue_deletes(self):
        device = self.device
        delete_all, ids = services.claim_delete_batch(device)
        if delete_all:
            self._enqueue(
                {"cmd": "cleanuser"},
                lambda ok, reply: ok and services.apply_delete_result(device, True, []),
            )
            return
        state = {"left": len(ids), "deleted": []}

        def make_done(user_id):
            def done(ok, reply):
                state["left"] -= 1
                if ok:
                    state["deleted"].append(user_id)
                if state["left"] == 0 and state["deleted"]:
                    services.apply_delete_result(device, False, state["deleted"])

            return done

        for user_id in ids:
            # backupnum 13 removes every credential of the user
            self._enqueue(
                {"cmd": "deleteuser", "enrollid": user_id, "backupnum": 13},
                make_done(user_id),
            )
