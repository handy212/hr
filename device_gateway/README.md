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
