"""Post-operative reservation endpoints."""

import frappe

from hospital_operations.api.common import require_api_role, require_value
from hospital_operations.bed_allocation import post_operative
from hospital_operations.bed_allocation.transaction import (
    ReservationConflict,
    ReservationError,
)
from hospital_operations.utils.roles import (
    ADMISSION_COORDINATOR,
    BED_MANAGER,
    NURSING_SUPERVISOR,
    OPERATIONS_ADMIN,
)

CLINICAL_ROLES = (
    BED_MANAGER,
    ADMISSION_COORDINATOR,
    NURSING_SUPERVISOR,
    OPERATIONS_ADMIN,
)
OVERRIDE_ROLES = (BED_MANAGER, NURSING_SUPERVISOR, OPERATIONS_ADMIN)


@frappe.whitelist(methods=["POST"])
def create(
    allocation_request=None,
    bed=None,
    start_at=None,
    expires_at=None,
    hold_hours=4,
    idempotency_key=None,
    reason=None,
):
    """Create a future post-operative bed hold."""

    require_api_role(*CLINICAL_ROLES)
    require_value(allocation_request, "allocation_request")
    require_value(bed, "bed")
    return _run(
        post_operative.create_reservation,
        allocation_request,
        bed,
        start_at=start_at,
        expires_at=expires_at,
        hold_hours=hold_hours,
        idempotency_key=idempotency_key,
        reason=reason,
    )


@frappe.whitelist(methods=["POST"])
def reschedule(
    reservation=None, start_at=None, expires_at=None, hold_hours=4, reason=None
):
    """Move an active post-operative hold to a new window."""

    require_api_role(*CLINICAL_ROLES)
    require_value(reservation, "reservation")
    require_value(start_at, "start_at")
    return _run(
        post_operative.reschedule,
        reservation,
        start_at,
        expires_at=expires_at,
        hold_hours=hold_hours,
        reason=reason,
    )


@frappe.whitelist(methods=["POST"])
def cancel(reservation=None, reason=None):
    """Cancel an active post-operative hold."""

    require_api_role(*CLINICAL_ROLES)
    require_value(reservation, "reservation")
    return _run(post_operative.cancel, reservation, reason=reason)


@frappe.whitelist(methods=["POST"])
def confirm(reservation=None, idempotency_key=None, from_bed=None):
    """Convert a post-operative hold into an admission or transfer."""

    require_api_role(*CLINICAL_ROLES)
    require_value(reservation, "reservation")
    return _run(
        post_operative.confirm,
        reservation,
        idempotency_key=idempotency_key,
        from_bed=from_bed,
    )


@frappe.whitelist(methods=["POST"])
def preempt(
    reservation=None,
    emergency_request=None,
    clinical_approval_reference=None,
    reason=None,
):
    """Release a post-operative hold for a clinically approved emergency."""

    require_api_role(*OVERRIDE_ROLES)
    require_value(reservation, "reservation")
    require_value(emergency_request, "emergency_request")
    return _run(
        post_operative.preempt,
        reservation,
        emergency_request,
        clinical_approval_reference=clinical_approval_reference,
        reason=reason,
    )


def _run(func, *args, **kwargs):
    try:
        result = func(*args, **kwargs)
    except ReservationConflict as exc:
        frappe.throw(str(exc), ReservationConflict)
    except ReservationError as exc:
        frappe.throw(str(exc), ReservationError)
    return _serialize_result(result)


def _serialize_result(result):
    payload = dict(result)
    for fieldname in ("start_at", "expires_at"):
        if payload.get(fieldname):
            payload[fieldname] = str(payload[fieldname])
    return payload
