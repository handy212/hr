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
    device_status = models.JSONField(
        default=dict, blank=True, editable=False
    )  # relay/door/alarm state from heartbeats

    def __str__(self):
        return f"{self.name or self.sn} ({self.sn})"


class TerminalEmployee(HorillaModel):
    """Maps a Horilla employee to the numeric user ID stored on a device."""

    objects = HorillaCompanyManager("device__company_id")

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="terminal_employees"
    )
    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="terminal_ids"
    )
    device_user_id = models.PositiveBigIntegerField(verbose_name=_("Device user ID"))
    synced_at = models.DateTimeField(null=True, blank=True)

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
    result = models.JSONField(default=dict, blank=True)

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

    device = models.ForeignKey(
        TerminalDevice, on_delete=models.CASCADE, related_name="system_records"
    )
    record_id = models.BigIntegerField()
    record_type = models.IntegerField()
    record_date = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["device", "record_id"], name="unique_terminal_system_record"
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
