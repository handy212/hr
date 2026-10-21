"""
Constants and helpers from the FC terminal HTTP v2 docking protocol (doc v6.0)
that are shared by the device-facing endpoints and the management UI.
"""

from django.utils.translation import gettext_lazy as _

# Parameters the device reports but must never be sent back to it.
READ_ONLY_PARAMETERS = frozenset(
    {
        "Success",
        "DeviceSN",
        "SN",
        "RelayStatus",
        "KeepOpenStatus",
        "DoorSensorStatus",
        "LockDoorStatus",
        "AlarmStatus",
        "PeopleStorageInfo",
        "RecordStorageInfo",
        "FirmwareVerson",
        "FingerprintVerson",
        "FaceVerson",
        "Manufacturer",
        "ManufacturerPhone",
        "Website",
        "ProductionDate",
        "OEMText",
        "FunctionList",
        "RunDays",
        "FormatCount",
        "WatchDogCount",
        "WiredMAC",
        "WIFIMAC",
    }
)

# Parameters that, if changed remotely, can cut the device off from this
# server or the network. They are shown read-only in the UI and never edited.
LOCKOUT_PARAMETERS = frozenset(
    {
        "ConnectPassword",
        "UseWired",
        "WiredDHCP",
        "WiredIP",
        "WiredIPMask",
        "WiredGteway",
        "WiredDNS",
        "UseWIFI",
        "WIFIDHCP",
        "WIFIIP",
        "WIFIIPMask",
        "WIFIGteway",
        "WIFIDNS",
        "WIFIAPName",
        "WIFIAPPassword",
        "UseHTTPClient",
        "HTTPClient_ProtocolType",
        "HTTPClient_ServerAddr",
        "HTTPClient_KeepaliveTime",
        "HTTPClient_UseGZIP",
    }
)

# (key, label, kind, extra). kind: bool | int | float | str | choice | json
PARAMETER_GROUPS = [
    (
        _("General"),
        [
            ("DeviceName", _("Device name"), "str", {}),
            ("Language", _("Language"), "choice", {"choices": [(0, "中文"), (1, "English")]}),
            ("TimeZone", _("Time zone (UTC offset)"), "int", {"min": -12, "max": 14}),
            ("UseNTP", _("Sync time via NTP"), "bool", {}),
            ("Volume", _("Volume"), "int", {"min": 0, "max": 10}),
            ("Voice", _("Voice prompts"), "bool", {}),
            ("DisplayBrightness", _("Display brightness"), "int", {"min": 0, "max": 10}),
            ("FillLight", _("Fill light mode"), "int", {"min": 0, "max": 3}),
            ("ShowPersonPhoto", _("Show person photo"), "bool", {}),
            ("ShowPersonName", _("Show person name"), "bool", {}),
            ("PlayPersonName", _("Play person name"), "bool", {}),
            ("UnregisteredWarn", _("Warn on unregistered person"), "bool", {}),
            ("AutoRestart", _("Daily auto restart"), "bool", {}),
            ("AutoRestartTime", _("Auto restart time (HH:MM)"), "str", {}),
            ("MenuPassword", _("On-device menu password"), "str", {}),
        ],
    ),
    (
        _("Access control"),
        [
            ("VerificationType", _("Verification type"), "int", {"min": 0, "max": 20}),
            ("ReleaseTime", _("Door release time (s)"), "int", {"min": 1, "max": 255}),
            ("OpenInterval", _("Minimum open interval (s)"), "int", {"min": 0, "max": 255}),
            ("OpenInterval_SaveRecord", _("Record repeated passes"), "bool", {}),
            ("FreeOpen", _("Free open (no verification)"), "bool", {}),
            ("Relay", _("Relay mode"), "int", {"min": 0, "max": 1}),
            ("MultiPerson", _("Multi-person verification"), "bool", {}),
            ("OverdueRemind", _("Remind before expiry"), "bool", {}),
            ("OverdueRemind_Day", _("Expiry reminder days"), "int", {"min": 0, "max": 255}),
            ("TimingOpen", _("Timed normally-open"), "bool", {}),
            ("TimingLocked", _("Timed locking"), "bool", {}),
        ],
    ),
    (
        _("Recognition"),
        [
            ("FaceThreshold", _("Face threshold"), "int", {"min": 1, "max": 100}),
            ("FaceDistance", _("Face distance level"), "int", {"min": 0, "max": 10}),
            ("FaceIR", _("Infrared liveness"), "bool", {}),
            ("FaceIRThreshold", _("Liveness threshold"), "int", {"min": 0, "max": 100}),
            ("FaceMask", _("Mask detection"), "bool", {}),
            ("FaceMaskThreshold", _("Mask threshold"), "int", {"min": 0, "max": 100}),
            ("FPComparison", _("Fingerprint threshold"), "int", {"min": 0, "max": 100}),
            ("UseBodyTemperature", _("Body temperature check"), "bool", {}),
            ("UseFahrenheitDisplay", _("Show Fahrenheit"), "bool", {}),
            ("TemperatureCompensate", _("Temperature compensation"), "float", {}),
            ("TemperatureAlarmThreshold", _("Temperature alarm threshold"), "float", {}),
        ],
    ),
    (
        _("Alarms"),
        [
            ("FireAlarm", _("Fire alarm input"), "bool", {}),
            ("BlacklistAlarm", _("Blacklist alarm"), "bool", {}),
            ("AntiDisassemblyAlarm", _("Tamper alarm"), "bool", {}),
            ("DoorSensorAlarm", _("Door sensor alarm"), "bool", {}),
            ("DoorLongOpenAlarm", _("Door open too long alarm"), "bool", {}),
            ("DoorLongOpenTime", _("Door open limit (s)"), "int", {"min": 0, "max": 65535}),
            ("IllegalVerificationAlarm", _("Illegal verification alarm"), "bool", {}),
            ("IllegalVerificationAlarmLimit", _("Illegal attempts limit"), "int", {"min": 0, "max": 255}),
            ("UseUserCloseAlarm", _("Allow users to dismiss alarm"), "bool", {}),
        ],
    ),
    (
        _("Records"),
        [
            ("RecordAutoCycle", _("Overwrite oldest records when full"), "bool", {}),
            ("SaveUnregistered", _("Keep unregistered attempts"), "bool", {}),
            ("SaveRecordPicture", _("Keep record pictures"), "bool", {}),
        ],
    ),
    (
        _("Schedules (JSON)"),
        [
            ("TimeGroups", _("Time groups"), "json", {}),
            ("Holidays", _("Holidays"), "json", {}),
            ("AlarmClocks", _("Alarm clocks"), "json", {}),
            ("TimingOpen_timegroup", _("Timed open schedule"), "json", {}),
            ("TimingLocked_timegroup", _("Timed lock schedule"), "json", {}),
            ("DoorSensorAlarmTimegroup", _("Door sensor alarm schedule"), "json", {}),
        ],
    ),
]

PARAMETER_FIELDS = {
    key: (label, kind, extra)
    for _group, fields in PARAMETER_GROUPS
    for key, label, kind, extra in fields
}

PERSON_ERROR_CODES = {
    1: _("Invalid personnel parameters"),
    2: _("Photo URL download failed"),
    3: _("Photo too large (must be under 500 KB)"),
    4: _("Face feature download failed"),
    5: _("Fingerprint download failed"),
    6: _("Palm vein download failed"),
    20: _("Duplicate face feature"),
    21: _("Duplicate fingerprint"),
    22: _("Duplicate palm vein"),
    23: _("Duplicate card number"),
    24: _("Device personnel storage is full"),
    25: _("Device database error"),
    26: _("Device failed to save the photo"),
    27: _("Face feature format error"),
    28: _("Fingerprint format error"),
    29: _("Palm vein format error"),
    30: _("Photo not recognizable (no clear face)"),
    31: _("Device failed to save the fingerprint"),
    32: _("Device failed to save the palm vein"),
}

REGISTER_TYPES = {
    1: _("Card"),
    2: _("Password"),
    3: _("Fingerprint"),
    4: _("Face"),
    5: _("Palm vein"),
}

REGISTER_RESULTS = {
    1: _("Registered"),
    2: _("Cancelled on device"),
    3: _("Duplicate credential"),
    4: _("Fingerprint not supported"),
    5: _("Palm vein not supported"),
    6: _("Device busy"),
}

DOOR_SENSOR_EVENTS = {
    1: _("Door opened"),
    2: _("Door closed"),
    3: _("Door sensor alarm detection started"),
    4: _("Door sensor alarm detection stopped"),
    5: _("Door not closed properly"),
    6: _("Opened with exit button"),
    7: _("Exit button pressed while locked"),
    8: _("Exit button pressed while controller expired"),
}

SYSTEM_EVENTS = {
    1: _("Software open door"),
    2: _("Software close door"),
    3: _("Software normally open"),
    4: _("Controller entered normally-open mode"),
    5: _("Controller closed the door automatically"),
    6: _("Exit button held: normally open"),
    7: _("Exit button held: normally closed"),
    8: _("Software lock"),
    9: _("Software unlock"),
    10: _("Timed lock engaged"),
    11: _("Timed lock released"),
    12: _("Alarm lock"),
    13: _("Alarm lock released"),
    14: _("Alarm: illegal authentication"),
    15: _("Alarm: door sensor"),
    16: _("Alarm: forced (duress)"),
    17: _("Alarm: open timeout"),
    18: _("Alarm: blacklist"),
    19: _("Alarm: fire"),
    20: _("Alarm: tamper"),
    21: _("Illegal authentication alarm cleared"),
    22: _("Door sensor alarm cleared"),
    23: _("Forced alarm cleared"),
    24: _("Open timeout alarm cleared"),
    25: _("Blacklist alarm cleared"),
    26: _("Fire alarm cleared"),
    27: _("Tamper alarm cleared"),
    28: _("System powered on"),
    29: _("System reset (watchdog)"),
    30: _("Device formatted"),
    31: _("Card reader reversed"),
    32: _("Card reader circuit fault"),
    33: _("Unrecognized card reader"),
    34: _("Network cable disconnected"),
    35: _("Network cable connected"),
    36: _("Wi-Fi connected"),
    37: _("Wi-Fi disconnected"),
    38: _("Bluetooth door open"),
    39: _("Roll call timeout"),
    40: _("All personnel cleared from device menu"),
    41: _("Personnel backed up to USB"),
    42: _("Personnel imported from USB"),
    43: _("Indoor unit remote door open"),
    44: _("All records deleted"),
    45: _("All personnel deleted"),
}

ALARM_EVENT_TYPES = frozenset({14, 15, 16, 17, 18, 19, 20})


def system_event_name(category, record_type):
    names = DOOR_SENSOR_EVENTS if category == 1 else SYSTEM_EVENTS
    return names.get(record_type, _("Event #%(type)s") % {"type": record_type})
