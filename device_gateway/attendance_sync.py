"""
Convert terminal punch records into Horilla attendance entries.

Follows the same pattern as anviz_biometric_attendance_logs
(biometric/views.py:2292): resolve the employee, build the fake request
from attendance.methods.utils.Request, and call clock_in/clock_out so
shift/grace/overtime logic applies exactly like other device types.
"""

import logging
from datetime import datetime

from django.db import transaction
from django.utils import timezone as dj_timezone

from attendance.methods.utils import Request
from attendance.models import AttendanceActivity
from attendance.views.clock_in_out import clock_in, clock_out
from employee.models import Employee

from .models import TerminalEmployee, TerminalPunchLog
from saas.device_access import tenant_device_operation

logger = logging.getLogger(__name__)

# RecordType values (spec §RecordType): verification modes that passed.
# Failed states (15-24, 26-30, 45, 48, 49) are logged but never create
# attendance.
PASSING_RECORD_TYPES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
                        25, 29, 31, 32, 36, 37, 38, 39, 40, 41, 42, 43, 46, 47}


def resolve_employee(device, user_id, code=None):
    """Map a device-side UserID to an Employee via TerminalEmployee,
    falling back to badge_id."""
    if user_id:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            uid = None
        if uid is not None:
            mapping = TerminalEmployee.objects.filter(
                device=device, device_user_id=uid,
                employee__employee_work_info__company_id=device.company_id_id
            ).select_related("employee", "employee__employee_user_id").first()
            if mapping:
                return mapping.employee
    if code:
        return Employee.objects.filter(badge_id=code).first()
    return None


@tenant_device_operation
@transaction.atomic
def process_punch(device, record, photo=None, retry=False):
    """
    record: parsed RecordDetail dict. Returns "created", "duplicate",
    "ignored" (non-passing type), or "unmapped" (no employee found).
    """
    record_id = record.get("RecordID")
    record_type = record.get("RecordType")
    ts = record.get("RecordDate")
    if record_id is None or ts is None:
        return "ignored"

    punch_dt = datetime.fromtimestamp(int(ts), tz=dj_timezone.get_current_timezone())

    log, created = TerminalPunchLog.objects.get_or_create(
        device=device,
        record_id=int(record_id),
        defaults={
            "record_type": int(record_type or 0),
            "record_date": punch_dt,
            "user_id": str(record.get("UserID") or ""),
            "raw": record,
            "photo": photo,
        },
    )
    log = TerminalPunchLog.objects.select_for_update().get(pk=log.pk)
    if not created and (not retry or log.attendance_created):
        if log.attendance_created:
            return "duplicate"
        return "unmapped" if log.employee_id is None else "ignored"

    if int(record_type or 0) not in PASSING_RECORD_TYPES:
        return "ignored"

    employee = resolve_employee(device, record.get("UserID"), record.get("Code"))
    if not employee:
        logger.warning(
            "device_gateway: punch %s on %s has no mapped employee (UserID=%s)",
            record_id, device.sn, record.get("UserID"),
        )
        return "unmapped"
    log.employee = employee
    log.save(update_fields=["employee"])

    request_data = Request(
        user=employee.employee_user_id,
        date=punch_dt.date(),
        time=punch_dt.time(),
        datetime=punch_dt,
    )

    is_entry = record.get("IsEntry", 1)
    direction = device.direction
    try:
        if direction == "in":
            clock_in(request_data)
        elif direction == "out":
            clock_out(request_data)
        elif direction == "alternate":
            last_activity = (
                AttendanceActivity.objects.filter(
                    employee_id=employee, attendance_date=punch_dt.date()
                )
                .order_by("-in_datetime", "-out_datetime")
                .first()
            )
            if not last_activity or last_activity.clock_out:
                clock_in(request_data)
            else:
                clock_out(request_data)
        else:  # system: trust the device's IsEntry flag
            if int(is_entry) == 1:
                clock_in(request_data)
            else:
                clock_out(request_data)
    except Exception:
        logger.exception(
            "device_gateway: clock_in/clock_out failed for punch %s "
            "on %s (employee=%s)",
            record_id, device.sn, employee.pk,
        )
        return "error"

    log.attendance_created = True
    log.save(update_fields=["attendance_created"])
    return "created"
