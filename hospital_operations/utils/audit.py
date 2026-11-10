import json

import frappe
from frappe.utils import now

MAX_REASON_LENGTH = 500


def log_event(
    event_type,
    *,
    bed=None,
    patient=None,
    inpatient_record=None,
    allocation_request=None,
    previous_state=None,
    new_state=None,
    reason=None,
    correlation_id=None,
    details=None,
    actor=None,
):
    """Append a minimal, non-clinical audit event.

    The event table is append-only. Callers must not include diagnosis,
    prescriptions or other clinical detail in ``details``.
    """
    payload = {
        "doctype": "Bed Allocation Event",
        "event_type": event_type,
        "bed": bed,
        "patient_reference": patient,
        "inpatient_record": inpatient_record,
        "allocation_request": allocation_request,
        "previous_state": previous_state,
        "new_state": new_state,
        "reason": (reason or "")[:MAX_REASON_LENGTH] or None,
        "correlation_id": correlation_id,
        "actor": actor or frappe.session.user,
        "timestamp": now(),
        "details": json.dumps(details or {}, sort_keys=True, default=str),
    }
    event = frappe.get_doc(payload)
    event.insert(ignore_permissions=True)
    return event.name
