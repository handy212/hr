"""
Shared request helpers for the FC terminal HTTP v2 docking protocol.

The spec is inconsistent (``Success`` values, typo'd field names, optional
gzip on multipart parts); every view funnels through these helpers so the
quirks live in exactly one place.

Response convention used throughout:
- HTTP 200 + {"Success": 1}               -> ok
- HTTP 200 + {"Success": 0, "Message": x} -> processed but refused/failed
- HTTP 200 + {"Success": 401, ...}        -> device SN unknown/inactive
"""

import gzip
import json
import logging

from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from .models import TerminalDevice

logger = logging.getLogger(__name__)

SUCCESS = 1
FAILURE = 0
UNAUTHORIZED = 401


class DeviceRejected(APIException):
    """Spec-mandated refusal, delivered as HTTP 200 with a Success code."""

    status_code = 200

    def __init__(self, message, code=FAILURE):
        super().__init__(detail=message)
        # Assign after super(): APIException coerces leaf values to
        # ErrorDetail strings, but the firmware expects numeric codes.
        self.detail = {"Success": code, "Message": message}


def ok(**extra):
    return Response({"Success": SUCCESS, **extra})


def fail(message="", code=FAILURE, **extra):
    return Response({"Success": code, "Message": message, **extra})


def get_sn(request):
    """SN arrives as JSON field, multipart form field, or query param."""
    sn = None
    if request.content_type and "json" in request.content_type:
        try:
            sn = request.data.get("SN")
        except Exception:
            sn = None
    if not sn:
        sn = request.POST.get("SN") or request.GET.get("SN")
    return (sn or "").strip()


def authenticate_device(request):
    """
    Resolve the calling device. Returns (device, None) or raises
    DeviceRejected.

    Unknown/inactive SNs and bad connect passwords get the spec-mandated
    {"Success": 401} — the device keeps heartbeating but stops sending
    records, which is the safest failure mode.
    """
    sn = get_sn(request)
    if not sn:
        raise DeviceRejected("Missing SN")
    # Device requests have no staff session; resolve SN across companies.
    device = TerminalDevice.all_objects.filter(sn=sn).first()
    if not device or not device.is_active:
        raise DeviceRejected(
            "Connected to server, but device is not activated", code=UNAUTHORIZED
        )
    if device.connect_password:
        sent = (
            request.data.get("ConnectPassword")
            or request.POST.get("ConnectPassword")
            or ""
        )
        if sent != device.connect_password:
            raise DeviceRejected("Bad ConnectPassword", code=UNAUTHORIZED)
    from saas.access import scope_is_suspended

    if not device.company_id_id or scope_is_suspended([device.company_id_id]):
        raise DeviceRejected("Tenant access is disabled", code=UNAUTHORIZED)
    device.last_seen = timezone.now()
    device.is_online = True
    fields = ["last_seen", "is_online"]
    ip = request.META.get("REMOTE_ADDR") or None
    if ip and ip != device.ip_address:
        device.ip_address = ip
        fields.append("ip_address")
    device.save(update_fields=fields)
    return device


def parse_json_field(request, *names):
    """
    Extract a JSON payload from a multipart form field that may be
    gzip-compressed (the spec allows Content-Encoding: gzip on the part).
    Tries each candidate name (spec examples use both RecordDetail and
    recordJson, Photo and pic).
    """
    for name in names:
        if name not in request.data and name not in request.FILES:
            continue
        value = request.data.get(name)
        if value is None:
            continue
        if hasattr(value, "read"):  # UploadedFile
            blob = value.read()
        elif isinstance(value, (bytes, bytearray)):
            blob = bytes(value)
        else:
            blob = str(value).encode()
        if blob[:2] == b"\x1f\x8b":
            blob = gzip.decompress(blob)
        try:
            return json.loads(blob.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning("device_gateway: bad JSON in field %s: %s", name, e)
            return None
    return None


def get_photo_file(request):
    """Spec examples name the photo part both 'Photo' and 'pic'."""
    return request.FILES.get("Photo") or request.FILES.get("pic")
