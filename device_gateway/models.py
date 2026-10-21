from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from horilla.models import HorillaModel


class TerminalDevice(HorillaModel):
    """
    An FC-series face recognition access terminal talking the vendor
    "HTTP v2 docking protocol" (device polls the server via heartbeat).
    """

    DIRECTION_CHOICES = [
        ("in", _("In")),
        ("out", _("Out")),
        ("alternate", _("Alternate In/Out")),
        ("system", _("System (use IsEntry flag)")),
    ]

    objects = HorillaCompanyManager()
    all_objects = models.Manager()

    sn = models.CharField(max_length=30, unique=True, verbose_name=_("Device SN"))
    name = models.CharField(max_length=100, blank=True, verbose_name=_("Name"))
    company_id = models.ForeignKey(
        "base.Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="terminal_devices",
        verbose_name=_("Company"),
    )
    connect_password = models.CharField(
        max_length=64,
        blank=True,
        help_text=_(
            "Optional shared secret. When set, the device must send it as "
            "ConnectPassword on every request."
        ),
    )
    direction = models.CharField(
        max_length=20, choices=DIRECTION_CHOICES, default="system"
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    is_online = models.BooleanField(default=False, editable=False)
    firmware_version = models.CharField(max_length=50, blank=True, editable=False)
    work_parameters = models.JSONField(
        default=dict, blank=True, verbose_name=_("Working parameters")
    )
    params_dirty = models.BooleanField(
        default=False,
        help_text=_("Set to push the stored working parameters to the device."),
    )
    params_refresh_requested = models.BooleanField(
        default=False,
        editable=False,
        help_text=_("Ask the device to re-upload its working parameters."),
    )
    last_params_at = models.DateTimeField(null=True, blank=True, editable=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True, editable=False)
    device_status = models.JSONField(
        default=dict, blank=True, editable=False
    )  # relay/door/alarm state from heartbeats

    @property
    def storage_info(self):
        """People/record capacity as reported in the uploaded parameters."""
        params = self.work_parameters or {}
        return {
            "people": params.get("PeopleStorageInfo") or {},
            "records": params.get("RecordStorageInfo") or {},
        }

    @property
    def active_alarms(self):
        alarm = (self.device_status or {}).get("AlarmStatus") or ""
        return [a.strip() for a in str(alarm).strip('"').split(",") if a.strip()]

    def __str__(self):
        return f"{self.name or self.sn} ({self.sn})"


class TerminalEmployee(HorillaModel):
    """Maps a Horilla employee to the numeric user ID stored on a device."""

    objects = HorillaCompanyManager("device__company_id")

    ACCESS_TYPE_CHOICES = [
        (0, _("Normal")),
        (1, _("Administrator")),
        (2, _("Blacklist")),
    ]

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="terminal_employees"
    )
    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="terminal_ids"
    )
    device_user_id = models.PositiveBigIntegerField(verbose_name=_("Device user ID"))
    synced_at = models.DateTimeField(null=True, blank=True)
    card_num = models.CharField(max_length=20, blank=True, verbose_name=_("Card number"))
    access_type = models.PositiveSmallIntegerField(
        choices=ACCESS_TYPE_CHOICES, default=0, verbose_name=_("Role")
    )
    expiration_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Access expires"),
        help_text=_("Leave empty for no expiry."),
    )
    open_times = models.PositiveIntegerField(
        default=65535,
        validators=[MaxValueValidator(65535)],
        verbose_name=_("Allowed passes"),
        help_text=_("65535 = unlimited, 0 = no passage allowed."),
    )
    timegroup = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(64)],
        verbose_name=_("Time group"),
        help_text=_("Device time group 1-64. Empty = device default."),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "device_user_id"], name="unique_terminal_user"
            ),
            models.UniqueConstraint(
                fields=["device", "employee"], name="unique_terminal_employee"
            ),
        ]

    def __str__(self):
        return f"{self.employee} @ {self.device.sn} -> {self.device_user_id}"


class TerminalSyncTask(HorillaModel):
    """Pending personnel add/update/delete work for a device."""

    objects = HorillaCompanyManager("device__company_id")

    ACTION_CHOICES = [
        ("add", _("Add/Update")),
        ("delete", _("Delete")),
        ("delete_all", _("Delete all")),
    ]
    STATUS_CHOICES = [
        ("pending", _("Pending")),
        ("sent", _("Sent")),
        ("acked", _("Acked")),
        ("failed", _("Failed")),
    ]

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="sync_tasks"
    )
    employee = models.ForeignKey(
        "employee.Employee",
        on_delete=models.CASCADE,
        null=True,
        blank=True,  # null for delete_all
    )
    device_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, default="add")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    sent_at = models.DateTimeField(null=True, blank=True)
    fail_detail = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.action} {self.employee_id or 'ALL'} -> {self.device.sn} ({self.status})"


class TerminalCommand(HorillaModel):
    """Pending remote operation, handed to the device via Device/RemoteCommand."""

    objects = HorillaCompanyManager("device__company_id")

    STATUS_CHOICES = [
        ("pending", _("Pending")),
        ("sent", _("Sent")),
        ("acked", _("Acked")),
    ]

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="commands"
    )
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    sent_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)

    @property
    def summary(self):
        return ", ".join(self.payload.keys()) if self.payload else "-"

    def __str__(self):
        return f"cmd#{self.pk} {self.device.sn} ({self.status})"


class TerminalPunchLog(HorillaModel):
    """Raw punch-in records; unique (device, record_id) is the dedup key."""

    objects = HorillaCompanyManager("device__company_id")

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="punch_logs"
    )
    record_id = models.BigIntegerField()
    record_type = models.IntegerField()
    record_date = models.DateTimeField()
    user_id = models.CharField(max_length=20, blank=True)
    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.SET_NULL, null=True, blank=True
    )
    photo = models.ImageField(upload_to="terminal_punches/", null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    attendance_created = models.BooleanField(default=False, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "record_id"], name="unique_terminal_punch"
            ),
        ]
        ordering = ["-record_date"]


class TerminalSystemRecord(HorillaModel):
    """Door-sensor / system events from Record/UploadSystemRecord."""

    objects = HorillaCompanyManager("device__company_id")

    CATEGORY_DOOR_SENSOR = 1
    CATEGORY_SYSTEM = 2
    CATEGORY_CHOICES = [
        (CATEGORY_DOOR_SENSOR, _("Door sensor")),
        (CATEGORY_SYSTEM, _("System")),
    ]

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="system_records"
    )
    category = models.PositiveSmallIntegerField(
        choices=CATEGORY_CHOICES, default=CATEGORY_SYSTEM
    )
    record_id = models.BigIntegerField()
    record_type = models.IntegerField()
    record_date = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "category", "record_id"],
                name="unique_terminal_system_record_v2",
            ),
        ]
        ordering = ["-record_date"]


class TerminalSnapshot(HorillaModel):
    """Camera snapshot uploaded via Device/UploadSnapshoot."""

    objects = HorillaCompanyManager("device__company_id")

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="snapshots"
    )
    photo = models.ImageField(upload_to="terminal_snapshots/")

    class Meta:
        ordering = ["-created_at"]


class TerminalDevicePerson(HorillaModel):
    """Personnel record as stored on the device (from People/PushPeople)."""

    objects = HorillaCompanyManager("device__company_id")

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="roster"
    )
    user_id = models.PositiveBigIntegerField()
    name = models.CharField(max_length=100, blank=True)
    job = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    identity_card = models.CharField(max_length=64, blank=True)
    card_num = models.CharField(max_length=24, blank=True)
    access_type = models.PositiveSmallIntegerField(default=0)
    expiration_date = models.DateTimeField(null=True, blank=True)
    open_times = models.PositiveIntegerField(default=65535)
    has_face = models.BooleanField(default=False)
    has_photo = models.BooleanField(default=False)
    fingerprint_count = models.PositiveSmallIntegerField(default=0)
    palmvein_count = models.PositiveSmallIntegerField(default=0)
    photo = models.ImageField(upload_to="terminal_people/", null=True, blank=True)
    last_pushed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "user_id"], name="unique_terminal_roster_user"
            ),
        ]
        ordering = ["user_id"]

    def __str__(self):
        return f"{self.name or self.user_id} @ {self.device.sn}"
