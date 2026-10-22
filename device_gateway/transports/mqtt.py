"""
MQTT transport (face-terminal "MQTT backend docking protocol" v1.0).

Pure protocol logic with no dependency on an MQTT client library: the
``run_mqtt_gateway`` command owns the broker connection and calls
``handle_message`` for each inbound frame and ``Dispatcher.collect`` to find
out what to publish. Everything it stores goes through ``services`` so it
shares behaviour with the HTTP v2 endpoints.

Frame layout (both directions)::

    uint32  length of the (possibly gzip-compressed) JSON segment
    uint32  length of the JSON before compression
    bytes   JSON segment, UTF-8
    bytes   optional attachment (e.g. a photo): the rest of the message

The document does not state the byte order. Inbound frames are accepted in
either order and the order used for outbound frames is configurable.
"""

import base64
import gzip
import json
import logging
import struct
import time
from dataclasses import dataclass

from django.core.files.base import ContentFile
from django.utils import timezone

from .. import services
from ..attendance_sync import process_punch

logger = logging.getLogger(__name__)

HEADER_SIZE = 8

# RemoteCommand flags the MQTT firmware understands. Other keys queued by
# the UI (snapshot, elevator, firmware ...) only exist in the HTTP protocol.
REMOTE_COMMAND_KEYS = (
    "Restart",
    "Recover",
    "Opendoor",
    "Closealarm",
    "RepostRecord",
    "PushAllPeople",
    "QueryPeople",
    "ClearRecord",
)

# Minimum gap before the same housekeeping request is repeated to a device.
RESEND_INTERVAL = 30


class FrameError(ValueError):
    pass


@dataclass
class Outbound:
    cmd: str
    body: object = None
    cmd_id: str = ""
    file: bytes = b""


def encode_frame(cmd, body=None, cmd_id="", file=b"", use_gzip=False, byteorder="little"):
    message = {"cmd": cmd, "CmdTime": int(time.time())}
    if cmd_id:
        message["CmdID"] = str(cmd_id)
    if body is not None:
        message["body"] = body
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
    segment = gzip.compress(raw) if use_gzip else raw
    fmt = "<II" if byteorder == "little" else ">II"
    return struct.pack(fmt, len(segment), len(raw)) + segment + (file or b"")


def decode_frame(payload, byteorder="little"):
    """Return (message dict, attachment bytes). Raises FrameError."""
    if payload[:1] == b"{":  # tolerate bare JSON from simple simulators
        return _loads(payload), b""
    orders = ("<", ">") if byteorder == "little" else (">", "<")
    for order in orders:
        if len(payload) < HEADER_SIZE:
            break
        segment_len, raw_len = struct.unpack(order + "II", payload[:HEADER_SIZE])
        end = HEADER_SIZE + segment_len
        if segment_len == 0 or end > len(payload):
            continue
        segment = payload[HEADER_SIZE:end]
        try:
            if segment[:2] == b"\x1f\x8b":
                segment = gzip.decompress(segment)
            elif len(segment) != raw_len:
                continue
            return _loads(segment), payload[end:]
        except (OSError, EOFError, FrameError):
            continue
    raise FrameError("unrecognised MQTT frame")


def _loads(blob):
    try:
        message = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FrameError(str(exc)) from exc
    if not isinstance(message, dict):
        raise FrameError("frame is not a JSON object")
    return message


def _body(message):
    # The document capitalises the key inconsistently ("body" / "Body").
    body = message.get("body", message.get("Body"))
    return body if body is not None else {}


def _as_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def handle_message(device, message, attachment=b""):
    """Process one device message; returns the ``Outbound`` replies."""
    cmd = str(message.get("cmd") or message.get("Cmd") or "")
    cmd_id = str(message.get("CmdID") or "")
    body = _body(message)

    if cmd == "Will_DeviceOffline":
        device.is_online = False
        device.save(update_fields=["is_online"])
        return []

    device.last_seen = timezone.now()
    device.is_online = True
    device.save(update_fields=["last_seen", "is_online"])

    if cmd == "KeepAlive":
        services.save_keepalive_status(device, body)
        services.release_finished_commands(device)
    elif cmd == "UploadWorkSetting":
        services.save_work_settings(device, body)
    elif cmd == "PushWorkSettingACK":
        if int(body.get("Result", 1) or 0) != 1:
            logger.warning(
                "device_gateway: %s rejected parameters: %s",
                device.sn,
                body.get("ErrorMsg"),
            )
    elif cmd == "RemoteCommandACK":
        _ack_command(device, cmd_id)
    elif cmd == "PushPeopleACK":
        services.apply_people_result(device, _as_list(body.get("FailList")))
    elif cmd == "PushDeletePeopleACK":
        services.apply_delete_result(
            device,
            str(body.get("DeleteAll", "0")) == "1",
            _as_list(body.get("DeleteList")),
        )
    elif cmd == "UploadPeople":
        detail = body.get("Detail") or {}
        services.apply_device_person_push(
            device,
            body.get("PushType"),
            detail,
            body.get("UserID") or detail.get("UserID"),
            ContentFile(attachment, name="person.jpg") if attachment else None,
        )
        return [Outbound("UploadPeopleACK", cmd_id=cmd_id)]
    elif cmd == "UploadIdentifyRecord":
        photo = ContentFile(attachment, name="punch.jpg") if attachment else None
        if process_punch(device, body, photo=photo) == "error":
            return []  # no ACK: the device keeps the record and retries
        return [Outbound("UploadIdentifyRecordACK", cmd_id=cmd_id)]
    elif cmd == "UploadSystemRecord":
        try:
            category = int(body.get("RecordType") or 2)
        except (TypeError, ValueError):
            category = 2
        services.record_system_events(device, category, body.get("Records"))
        return [Outbound("UploadSystemRecordACK", cmd_id=cmd_id)]
    else:
        logger.info("device_gateway: ignoring MQTT command %r from %s", cmd, device.sn)
    return []


def _ack_command(device, cmd_id):
    if not cmd_id.startswith("c") or not cmd_id[1:].isdigit():
        return
    device.commands.filter(pk=int(cmd_id[1:]), status="sent").update(status="acked")


def _person_message(device, task):
    person = services.build_person_json(device, task)
    if not person:
        task.status = "failed"
        task.fail_detail = {"ErrMsg": "Employee no longer exists"}
        task.save(update_fields=["status", "fail_detail"])
        return None
    photo = person.pop("Photo", "")
    attachment = b""
    if photo:
        attachment = base64.b64decode(photo)
        person["Photo"] = f"{person['UserID']}.jpg"
    services.mark_task_sent(task)
    return Outbound("PushPeople", [person], cmd_id=f"p{task.pk}", file=attachment)


class Dispatcher:
    """Turns queued work for a device into protocol messages.

    The terminals are single-threaded, so at most one message per kind is in
    flight and housekeeping requests are throttled.
    """

    def __init__(self):
        self._last = {}

    def _due(self, device, kind):
        key = (device.pk, kind)
        now = time.monotonic()
        if now - self._last.get(key, -RESEND_INTERVAL) < RESEND_INTERVAL:
            return False
        self._last[key] = now
        return True

    def collect(self, device):
        out = []
        if device.params_dirty:
            out.append(
                Outbound(
                    "PushWorkSetting",
                    services.settings_to_push(device),
                    cmd_id=f"w{int(time.time())}",
                )
            )
        elif (
            device.params_refresh_requested or not device.work_parameters
        ) and self._due(device, "read"):
            out.append(Outbound("ReadWorkSetting", cmd_id=f"r{int(time.time())}"))

        services.release_finished_commands(device)
        if not device.commands.filter(status="sent").exists():
            command = services.pop_next_command(device)
            if command:
                payload = {
                    k: v for k, v in command.payload.items() if k in REMOTE_COMMAND_KEYS
                }
                if payload:
                    out.append(Outbound("RemoteCommand", payload, cmd_id=f"c{command.pk}"))
                else:
                    command.status = "acked"
                    command.result = {
                        "Result": "unsupported",
                        "Message": "Not available on MQTT terminals",
                    }
                    command.save(update_fields=["status", "result"])

        add_actions = ["add"]
        if not services.inflight_sync_tasks(device, add_actions).exists():
            task = (
                services.ready_sync_tasks(device, add_actions)
                .select_related("employee")
                .order_by("created_at")
                .first()
            )
            if task:
                message = _person_message(device, task)
                if message:
                    out.append(message)

        delete_actions = ["delete", "delete_all"]
        if (
            not services.inflight_sync_tasks(device, delete_actions).exists()
            and services.ready_sync_tasks(device, delete_actions).exists()
        ):
            delete_all, ids = services.claim_delete_batch(device)
            out.append(
                Outbound(
                    "PushDeletePeople",
                    {
                        "DeleteAll": 1 if delete_all else 0,
                        "DeleteCount": len(ids),
                        "DeleteList": ids,
                    },
                    cmd_id=f"d{int(time.time())}",
                )
            )
        return out
