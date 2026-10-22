# device_gateway — FC-series face-recognition terminal integration

Server-side implementation of the vendor **HTTP v2 docking protocol** for
FC-series face recognition access terminals (spec: `attendance/HTTP V2
Docking protocol of face recognition terminal(*).pdf`, doc v6.0).

Unlike the poll-based devices in `biometric/` (ZK, Anviz, COSEC), these
terminals **call us**: the device heartbeats every 15–30s and the heartbeat
response tells it what work is waiting.

## Device setup

On the terminal's web page, set the HTTP Client (V2) server address to:

    http://<server>:8000/api/terminal

Paths are appended by the firmware (`Device/Keepalive`,
`Record/UploadIdentifyRecord`, ...), including the spec's typos — the URL
conf here must NOT include a trailing path.

Recommended: keepalive 15–30s, and put the server behind TLS (the protocol
has no signing; `ConnectPassword` is the only shared secret and is sent in
the clear without HTTPS).

## Registering a device

Create a `TerminalDevice` row (Django admin or shell) with the device's SN,
before powering it on against the server. Unknown/inactive SNs get
`{"Success": 401}` — per spec the device keeps heartbeating but withholds
records, so no punches are lost once you activate it.

Optional `connect_password` must match the device's ConnectPassword setting.

For a terminal that already has people enrolled, open its Device Gateway detail
page and map each terminal `UserID` to the matching Horilla employee. Punches
from unmapped users are stored but cannot create attendance. After mapping,
use **Retry** next to each earlier pending punch to create attendance from the
saved record. The app never guesses an employee from a terminal name.

**Sync Employees** queues active employees from the device's company when a
company is assigned; an unassigned device includes all active employees.
The employee's `employee_profile` photo is sent inline when it is at most
400 KB. A sent personnel batch is retried after two minutes if the terminal
does not report a result; failed imports remain visible in Sync Tasks for
manual review and retry.

## Data flow

- `Device/Keepalive` → response flags (`AddPeople`, `DeletePeople`,
  `SyncParameter`, `Remote`) computed from `TerminalSyncTask` /
  `TerminalCommand` queues.
- `Record/UploadIdentifyRecord` → `TerminalPunchLog` (dedup on
  `(device, record_id)` — mandatory, `RepostRecord` resends everything) →
  employee resolved via `TerminalEmployee` → `clock_in`/`clock_out` exactly
  like the other biometric device types.
- `People/DownloadPeopleList` pushes employees to the device. Photos go
  **inline as base64**, never URLs: Horilla media is behind
  `protected_media` session auth a device can't satisfy.
- To queue work from code:

```python
from device_gateway.models import TerminalSyncTask, TerminalCommand
TerminalSyncTask.objects.create(device=dev, employee=emp, action="add")
TerminalCommand.objects.create(device=dev, payload={"Opendoor": 1})
```

## Spec quirks this implementation standardizes

- Success semantics: HTTP 200 + `{"Success": 1}` = ok, `0` = refused,
  `401` = unregistered SN. (The spec contradicts itself; this is the
  dominant reading.)
- The photo part is accepted as `Photo` or `pic`; record JSON as
  `RecordDetail` or `recordJson`; delete feedback as `DeleteList` or
  `DeletList`.
- Multipart JSON parts may be gzip-compressed; handled transparently.
- Failed verification types (unregistered, blacklist, expired, ...) are
  stored in `TerminalPunchLog` but never create attendance.

## Monitoring

`device_gateway.scheduler.mark_offline_devices` runs in the central
`run_scheduler` process and flips `is_online` off after 3 missed heartbeat
cycles (3 minutes).

## Full device management (HTTP V2 spec v6.0)

Apply the schema first: `python manage.py migrate device_gateway`.

- **Parameters** – the device reports its settings via `UploadWorkParameter`; the
  *Parameters* page edits them and queues a `SyncParameter` on the next keepalive.
  Read-only keys are never sent back, and network/server/`ConnectPassword` keys are
  not editable remotely so a device can't be locked out. *Refresh* asks the device to
  re-upload its parameters.
- **Remote commands** – open / keep-open / close / lock / unlock door, reboot, snapshot,
  close alarm, re-post records, pull people, clear records, factory reset, push
  firmware/file, query people, register credential (face/card/fingerprint/…), elevator.
  Clear records, factory reset, delete-all and push firmware need the
  `delete_terminaldevice` permission. Queued commands can be cancelled.
- **People** – per-employee access rules (card number, access type, expiry, open times,
  time group), sync/remove per mapping, and a roster of people actually stored on the
  device (from `PushPeople` / `RegisterIdentifyTicketResult`) with a one-click map for
  unmapped entries. Device error codes are translated into readable messages.
- **Activity page** – command history, door-sensor and system events (category from the
  top-level `RecordType`: 1 = door sensor, 2 = system), snapshots and the roster.
- **Reliability** – sent commands are acked after 60 s (registration commands wait for
  their result callback, 10 min timeout); unacknowledged people batches are retried.

## Multiple protocols, one gateway

Each device has a **Protocol** (set when registering it). All three share the
same models, attendance mapping, command queue and UI; only the transport
differs. Shared behaviour lives in `services.py`; each transport is a thin
adapter in `transports/`.

| Protocol | Who connects | Run |
|---|---|---|
| `http_v2` | Device polls Django (`/api/terminal/...`) | the web app |
| `mqtt` | Device connects to a broker; this bridge subscribes | `manage.py run_mqtt_gateway` |
| `ws_json` | Device connects to this server (ws://, port 7788) | `manage.py run_ws_gateway` |

Register the device with its SN **and the matching protocol**; unknown SNs or
a protocol mismatch are refused.

### MQTT (face terminal, MQTT doc v1.0)

    manage.py run_mqtt_gateway --host broker --port 1883 [--username u --password p --tls]

Also configurable with `DEVICE_GATEWAY_MQTT_*` environment variables (HOST,
PORT, USERNAME, PASSWORD, TLS, UPLOAD_TOPIC, PUBLISH_TOPIC, BYTE_ORDER). Point
the terminal at the same broker (default topics `/iot_hub/upload/<SN>` and
`/iot_hub/publish/<SN>`). The bridge uses the same record/person JSON as
HTTP v2, so punches, rosters, parameters, door/restart/clear/re-upload
commands and personnel push/delete work unchanged; photos are sent as the
binary attachment segment. Snapshot, elevator, firmware/file push and remote
registration have no MQTT equivalent and are marked unsupported. The document
does not state the frame byte order: inbound frames are accepted in either
order, outbound defaults to little endian (`--byte-order big` to change) —
verify against a real unit.

### WebSocket + JSON (cloud protocol v2.4)

    manage.py run_ws_gateway [--host 0.0.0.0 --port 7788]

Terminals send `reg`, then `sendlog` (punches → attendance, deduplicated on
user+time since the protocol has no record id) and `senduser` (roster).
Supported pushes: open door, reboot, factory reset (`initsys`), clear log,
pull roster/logs, time sync, personnel add (name, card, photo) and delete.
This protocol has no credentials and is plaintext; expose the port only to the
device network.
