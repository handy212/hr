"""Effective authorization scope for platform integrations and cache boundaries."""
from hashlib import sha256
import json

from django.utils.translation import get_language
from horilla.horilla_middlewares import _thread_locals, get_selected_company


def effective_company_ids():
    selected = get_selected_company()
    if not selected:
        return ()
    if selected != 'all':
        return (int(selected),)
    request = getattr(_thread_locals, 'request', None)
    ids = getattr(request, 'all_my_company_ids', None)
    if ids is None:
        ids = getattr(request, 'allowed_company_ids', None)
    # Unbounded legacy scope is never permission to expose a company directory.
    return tuple(sorted(int(value) for value in ids)) if ids is not None else ()


def scoped_cache_key(namespace, identifier):
    request = getattr(_thread_locals, 'request', None)
    user = getattr(request, 'user', None)
    scope = [get_selected_company(), effective_company_ids(), getattr(user, 'pk', None), get_language()]
    digest = sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    return f'{namespace}:v2:{identifier}:{digest}'

# Personal records with missing ownership are quarantined, never shared catalogs.
# This is deliberately explicit: nullable configuration/catalog rows need separate
# model-specific decisions and are not reclassified by name or relation guessing.
PRIVATE_MODELS = frozenset({
    'employee.employeetag', 'employee.actiontype', 'recruitment.skill',
    'pms.anonymousfeedback', 'payroll.encashmentgeneralsettings',
    'horilla_automations.mailautomation',
    'offboarding.offboardingtask', 'offboarding.employeetask', 'offboarding.exitreason',
    'offboarding.offboardingnote', 'horilla_meet.googlecredential', 'horilla_meet.googlemeeting',
    'horilla_meet.interviewmeetinglink', 'horilla_meet.pmsmeetinglink',
    'whatsapp.whatsappflowdetails', 'facedetection.employeefacedetection',
    'biometric.biometricemployees', 'biometric.cosecattendancearguments',
    'device_gateway.terminalemployee', 'device_gateway.terminalsynctask',
    'device_gateway.terminalcommand', 'device_gateway.terminalpunchlog',
    'device_gateway.terminalsystemrecord', 'device_gateway.terminalsnapshot',
    'employee.employee', 'employee.employeeworkinformation',
    'employee.employeebankdetails', 'employee.notefiles',
    'employee.employeenote', 'employee.bonuspoint',
    'employee.disciplinaryaction', 'employee.policymultiplefile',
    'attendance.attendance', 'attendance.attendanceactivity',
    'attendance.attendancelatecomeearlyout', 'attendance.workrecords',
    'payroll.contract', 'payroll.payslip',
    'recruitment.candidate', 'recruitment.stagenote', 'recruitment.stagefiles',
    'leave.leaverequest', 'leave.availableleave',
})


def permits_shared_rows(model):
    label = getattr(model, "_saas_original_model_label", model._meta.label_lower)
    return label not in PRIVATE_MODELS
