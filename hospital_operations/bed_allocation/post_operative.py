"""Time-windowed post-operative bed reservations.

A post-operative hold is a normal Bed Reservation with type POST_OPERATIVE. The
bed enters RESERVED immediately, so no other allocation can consume the
guaranteed postoperative bed. Confirmation revalidates eligibility and reuses
Marley admission or transfer as the only occupancy write boundary.
"""

import json

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from hospital_operations.bed_allocation import housekeeping, operational, repository
from hospital_operations.bed_allocation.eligibility import evaluate
from hospital_operations.bed_allocation.postop_windows import (
    DEFAULT_HOLD_HOURS,
    ReservationWindow,
    WindowError,
    default_window,
    windows_overlap,
)
from hospital_operations.bed_allocation.transaction import (
    ReservationConflict,
    ReservationError,
    lock_bed_states,
    lock_beds,
    lock_document_values,
    lock_inpatient_record,
    savepoint,
)
from hospital_operations.integrations import marley
from hospital_operations.utils.audit import log_event
from hospital_operations.utils.config import get_active_policy

ACTIVE = "ACTIVE"
CONFIRMED = "CONFIRMED"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"
RESERVED = "RESERVED"
ALLOCATED = "ALLOCATED"
WAITLISTED = "WAITLISTED"
POST_OPERATIVE = "POST_OPERATIVE"
OT_SOURCE = "OT"


class PostOperativeError(ReservationError):
    """A post-operative reservation operation is invalid."""


def create_reservation(
    allocation_request,
    bed,
    start_at=None,
    expires_at=None,
    hold_hours=DEFAULT_HOLD_HOURS,
    idempotency_key=None,
    reason=None,
    actor=None,
):
    """Create a future post-operative hold and reserve its physical bed."""

    allocation_request = _require_name(allocation_request, "allocation_request")
    bed = _require_name(bed, "bed")
    actor = actor or frappe.session.user
    window = _window(start_at, expires_at, hold_hours)
    if window.end_at <= now_datetime():
        raise PostOperativeError(_("The post-operative window has already ended."))

    key = idempotency_key or (
        f"postop:{allocation_request}:{bed}:"
        f"{window.start_at.isoformat()}:{window.end_at.isoformat()}"
    )

    with savepoint("ho_postop_create"):
        request_row = lock_document_values(
            "Bed Allocation Request",
            allocation_request,
            [
                "name",
                "patient",
                "inpatient_record",
                "status",
                "source",
            ],
        )
        if request_row.source != OT_SOURCE:
            raise PostOperativeError(
                _("Post-operative holds require an OT allocation request.")
            )
        if not request_row.inpatient_record:
            raise PostOperativeError(
                _("Allocation Request {0} has no Inpatient Record.").format(
                    allocation_request
                )
            )
        if request_row.status in (CANCELLED, ALLOCATED):
            raise ReservationConflict(
                _("Allocation Request {0} is {1}.").format(
                    allocation_request, request_row.status
                )
            )

        existing = _reservation_by_key(key)
        if existing:
            return _idempotent_existing(existing, request_row, bed, window)

        _assert_request_has_no_active_hold(allocation_request, exclude=None)
        lock_beds([bed])
        lock_bed_states([bed])
        operational.ensure_state(bed)

        context = _bed_context(bed)
        _assert_bed_ready(context)
        rows = _active_holds_for_bed(bed)
        _assert_window_clear(rows, window, exclude=None)

        eligibility = _evaluate(allocation_request, context)
        if not eligibility.eligible:
            details = "; ".join(item.message for item in eligibility.reasons)
            raise ReservationConflict(
                _("Bed {0} is not eligible: {1}").format(bed, details)
            )

        reservation = frappe.get_doc(
            {
                "doctype": "Bed Reservation",
                "allocation_request": allocation_request,
                "healthcare_service_unit": bed,
                "reservation_type": POST_OPERATIVE,
                "status": ACTIVE,
                "start_at": window.start_at,
                "expires_at": window.end_at,
                "idempotency_key": key,
                "inpatient_record": request_row.inpatient_record,
                "reason": reason or "Post-operative bed reservation",
                "created_by": actor,
            }
        ).insert(ignore_permissions=True)
        operational.mark_reserved(
            bed,
            reason=f"Post-operative hold {reservation.name}",
            expected_version=context.state_version,
            actor=actor,
        )
        _set_status("Bed Allocation Request", allocation_request, RESERVED)
        log_event(
            "RESERVATION_CREATED",
            bed=bed,
            patient=request_row.patient,
            inpatient_record=request_row.inpatient_record,
            allocation_request=allocation_request,
            new_state=RESERVED,
            reason=reason or "Post-operative bed reservation",
            correlation_id=reservation.name,
            details={
                "reservation": reservation.name,
                "reservation_type": POST_OPERATIVE,
                "start_at": str(window.start_at),
                "expires_at": str(window.end_at),
            },
            actor=actor,
        )

    return _result(reservation, idempotent=False)


def reschedule(
    reservation_name,
    start_at,
    expires_at=None,
    hold_hours=DEFAULT_HOLD_HOURS,
    reason=None,
    actor=None,
):
    """Move an active post-operative hold to a new, non-conflicting window."""

    reservation_name = _require_name(reservation_name, "reservation")
    actor = actor or frappe.session.user
    window = _window(start_at, expires_at, hold_hours)

    with savepoint("ho_postop_reschedule"):
        row = lock_document_values(
            "Bed Reservation",
            reservation_name,
            [
                "name",
                "allocation_request",
                "healthcare_service_unit",
                "reservation_type",
                "status",
                "start_at",
                "expires_at",
            ],
        )
        _assert_post_operative(row)
        if row.status != ACTIVE:
            raise ReservationConflict(
                _("Reservation {0} is {1}; only active holds can be moved.").format(
                    reservation_name, row.status
                )
            )

        lock_document_values(
            "Bed Allocation Request",
            row.allocation_request,
            ["name", "patient", "inpatient_record", "status"],
        )
        lock_beds([row.healthcare_service_unit])
        lock_bed_states([row.healthcare_service_unit])

        rows = _active_holds_for_bed(
            row.healthcare_service_unit, exclude=reservation_name
        )
        _assert_window_clear(rows, window, exclude=reservation_name)

        old_window = {
            "start_at": str(row.start_at),
            "expires_at": str(row.expires_at),
        }
        reservation = frappe.get_doc("Bed Reservation", reservation_name)
        reservation.start_at = window.start_at
        reservation.expires_at = window.end_at
        if reason:
            reservation.reason = reason
        reservation.save(ignore_permissions=True)
        log_event(
            "RESERVATION_CREATED",
            bed=reservation.healthcare_service_unit,
            inpatient_record=reservation.inpatient_record,
            allocation_request=reservation.allocation_request,
            previous_state=ACTIVE,
            new_state=ACTIVE,
            reason=reason or "Post-operative reservation rescheduled",
            correlation_id=reservation.name,
            details={
                "reservation": reservation.name,
                "old_window": old_window,
                "start_at": str(window.start_at),
                "expires_at": str(window.end_at),
            },
            actor=actor,
        )
    return _result(reservation, idempotent=False)


def cancel(reservation_name, reason=None, actor=None):
    """Cancel a post-operative hold and release its bed when safe."""

    reservation_name = _require_name(reservation_name, "reservation")
    actor = actor or frappe.session.user
    reason = reason or "Post-operative reservation cancelled"

    with savepoint("ho_postop_cancel"):
        row = lock_document_values(
            "Bed Reservation",
            reservation_name,
            [
                "name",
                "allocation_request",
                "healthcare_service_unit",
                "reservation_type",
                "status",
            ],
        )
        _assert_post_operative(row)
        if row.status == CANCELLED:
            return _result(
                frappe.get_doc("Bed Reservation", reservation_name), idempotent=True
            )
        if row.status != ACTIVE:
            raise ReservationConflict(
                _("Reservation {0} is {1}; it cannot be cancelled.").format(
                    reservation_name, row.status
                )
            )

        lock_document_values(
            "Bed Allocation Request",
            row.allocation_request,
            ["name", "status"],
        )
        lock_beds([row.healthcare_service_unit])
        lock_bed_states([row.healthcare_service_unit])

        reservation = frappe.get_doc("Bed Reservation", reservation_name)
        reservation.status = CANCELLED
        reservation.reason = reason
        reservation.save(ignore_permissions=True)
        _set_status("Bed Allocation Request", row.allocation_request, CANCELLED)
        _release_bed_if_reserved(
            row.healthcare_service_unit,
            actor,
            f"Reservation {reservation_name} cancelled",
        )
        log_event(
            "RESERVATION_CANCELLED",
            bed=row.healthcare_service_unit,
            allocation_request=row.allocation_request,
            inpatient_record=reservation.inpatient_record,
            previous_state=ACTIVE,
            new_state=CANCELLED,
            reason=reason,
            correlation_id=reservation_name,
            actor=actor,
        )
    return _result(reservation, idempotent=False)


def confirm(reservation_name, idempotency_key=None, from_bed=None, actor=None):
    """Revalidate and convert a post-operative hold into occupancy."""

    reservation_name = _require_name(reservation_name, "reservation")
    actor = actor or frappe.session.user
    correlation_id = idempotency_key or reservation_name

    with savepoint("ho_postop_confirm"):
        row = lock_document_values(
            "Bed Reservation",
            reservation_name,
            [
                "name",
                "allocation_request",
                "healthcare_service_unit",
                "reservation_type",
                "status",
                "start_at",
                "expires_at",
                "inpatient_record",
            ],
        )
        _assert_post_operative(row)
        if row.status == CONFIRMED:
            return _result(
                frappe.get_doc("Bed Reservation", reservation_name), idempotent=True
            )
        if row.status != ACTIVE:
            raise ReservationConflict(
                _("Reservation {0} is {1}; it cannot be confirmed.").format(
                    reservation_name, row.status
                )
            )
        if get_datetime(row.expires_at) <= now_datetime():
            raise ReservationConflict(
                _("Reservation {0} has expired.").format(reservation_name)
            )
        if not row.inpatient_record:
            raise PostOperativeError(
                _("Reservation {0} has no Inpatient Record.").format(reservation_name)
            )

        request_row = lock_document_values(
            "Bed Allocation Request",
            row.allocation_request,
            ["name", "patient", "inpatient_record", "status"],
        )
        if request_row.inpatient_record != row.inpatient_record:
            raise ReservationConflict(
                _("Allocation Request {0} does not belong to {1}.").format(
                    row.allocation_request, row.inpatient_record
                )
            )

        lock_inpatient_record(row.inpatient_record)
        active_rows = marley.get_active_occupancies_for_update(row.inpatient_record)
        if len(active_rows) > 1:
            raise ReservationConflict(
                _("Inpatient Record {0} has multiple active beds.").format(
                    row.inpatient_record
                )
            )
        current_bed = active_rows[0].service_unit if active_rows else None
        if from_bed and current_bed != from_bed:
            raise ReservationConflict(
                _("Inpatient Record {0} is in {1}, not {2}.").format(
                    row.inpatient_record, current_bed, from_bed
                )
            )

        target_bed = row.healthcare_service_unit
        locked_beds = [target_bed] if not current_bed else [current_bed, target_bed]
        lock_beds(locked_beds)
        lock_bed_states(locked_beds)
        operational.ensure_state(target_bed)

        reservation = frappe.get_doc("Bed Reservation", reservation_name)
        if reservation.status == CONFIRMED:
            return _result(reservation, idempotent=True)
        if reservation.status != ACTIVE:
            raise ReservationConflict(
                _("Reservation {0} is {1}; it cannot be confirmed.").format(
                    reservation_name, reservation.status
                )
            )

        if current_bed == target_bed:
            _mark_confirmed(
                reservation,
                row.allocation_request,
                actor,
                correlation_id,
                reason="Post-operative patient already occupies the reserved bed",
                idempotent=True,
            )
            return _result(reservation, idempotent=True)

        other_holds = _active_holds_for_bed(target_bed, exclude=reservation_name)
        if other_holds:
            raise ReservationConflict(
                _("Bed {0} has another active reservation.").format(target_bed)
            )

        context = _bed_context(target_bed)
        if context.state != RESERVED:
            raise ReservationConflict(
                _("Bed {0} is {1}; the post-operative hold is no longer valid.").format(
                    target_bed, context.state
                )
            )
        eligibility = _evaluate(row.allocation_request, context)
        if not eligibility.eligible:
            details = "; ".join(item.message for item in eligibility.reasons)
            raise ReservationConflict(
                _("Bed {0} is no longer eligible: {1}").format(target_bed, details)
            )

        if current_bed:
            log_event(
                "TRANSFER_INITIATED",
                bed=current_bed,
                patient=request_row.patient,
                inpatient_record=row.inpatient_record,
                allocation_request=row.allocation_request,
                previous_state="OCCUPIED",
                new_state=RESERVED,
                reason=f"Post-operative transfer to {target_bed}",
                correlation_id=correlation_id,
                details={"from_bed": current_bed, "to_bed": target_bed},
                actor=actor,
            )
            marley.transfer(row.inpatient_record, target_bed, from_bed=current_bed)
            operational.mark_vacated(
                current_bed,
                reason=f"Post-operative transfer to {target_bed}",
                actor=actor,
            )
            operational.require_cleaning(
                current_bed,
                reason=f"Cleaning required after post-operative transfer to {target_bed}",
                actor=actor,
            )
            housekeeping.ensure_task(
                current_bed,
                "DISCHARGE_CLEAN",
                reason=f"Cleaning required after post-operative transfer to {target_bed}",
                actor=actor,
            )
            operational.mark_occupied(
                target_bed,
                reason=f"Post-operative transfer from {current_bed}",
                actor=actor,
            )
            log_event(
                "TRANSFER_COMPLETED",
                bed=target_bed,
                patient=request_row.patient,
                inpatient_record=row.inpatient_record,
                allocation_request=row.allocation_request,
                previous_state=current_bed,
                new_state=target_bed,
                reason="Post-operative transfer completed",
                correlation_id=correlation_id,
                details={"from_bed": current_bed, "to_bed": target_bed},
                actor=actor,
            )
        else:
            marley.admit(row.inpatient_record, target_bed)
            operational.mark_occupied(
                target_bed,
                reason=f"Post-operative admission under reservation {reservation_name}",
                actor=actor,
            )
            log_event(
                "ADMISSION_LINKED",
                bed=target_bed,
                patient=request_row.patient,
                inpatient_record=row.inpatient_record,
                allocation_request=row.allocation_request,
                previous_state=RESERVED,
                new_state="OCCUPIED",
                reason="Marley post-operative admission completed",
                correlation_id=correlation_id,
                details={"reservation": reservation_name},
                actor=actor,
            )

        _mark_confirmed(
            reservation,
            row.allocation_request,
            actor,
            correlation_id,
            reason="Post-operative reservation confirmed",
            idempotent=False,
        )
    return _result(reservation, idempotent=False)


def preempt(
    reservation_name,
    emergency_request,
    clinical_approval_reference=None,
    reason=None,
    actor=None,
):
    """Release a post-operative hold for an approved emergency patient."""

    reservation_name = _require_name(reservation_name, "reservation")
    emergency_request = _require_name(emergency_request, "emergency_request")
    approval = (clinical_approval_reference or "").strip()
    if not approval:
        raise PostOperativeError(
            _("Emergency preemption requires a clinical approval reference.")
        )
    actor = actor or frappe.session.user
    reason = reason or "Emergency preemption"

    with savepoint("ho_postop_preempt"):
        emergency_row = lock_document_values(
            "Bed Allocation Request",
            emergency_request,
            ["name", "priority", "status", "patient", "inpatient_record"],
        )
        if emergency_row.priority != "Emergency":
            raise PostOperativeError(
                _("Only Emergency requests may preempt a post-operative hold.")
            )
        if emergency_row.status in (CANCELLED, ALLOCATED):
            raise ReservationConflict(
                _("Emergency Request {0} is {1}.").format(
                    emergency_request, emergency_row.status
                )
            )

        row = lock_document_values(
            "Bed Reservation",
            reservation_name,
            [
                "name",
                "allocation_request",
                "healthcare_service_unit",
                "reservation_type",
                "status",
            ],
        )
        _assert_post_operative(row)
        if row.status == CANCELLED:
            return _result(
                frappe.get_doc("Bed Reservation", reservation_name), idempotent=True
            )
        if row.status != ACTIVE:
            raise ReservationConflict(
                _("Reservation {0} is {1}; only an active hold can be preempted.").format(
                    reservation_name, row.status
                )
            )
        if row.allocation_request == emergency_request:
            raise PostOperativeError(
                _("A request cannot preempt its own reservation.")
            )

        lock_document_values(
            "Bed Allocation Request",
            row.allocation_request,
            ["name", "status"],
        )
        lock_beds([row.healthcare_service_unit])
        lock_bed_states([row.healthcare_service_unit])

        reservation = frappe.get_doc("Bed Reservation", reservation_name)
        reservation.status = CANCELLED
        reservation.reason = f"{reason} (approved: {approval})"
        reservation.save(ignore_permissions=True)
        _set_status("Bed Allocation Request", row.allocation_request, WAITLISTED)
        _release_bed_if_reserved(
            row.healthcare_service_unit,
            actor,
            f"Post-operative hold preempted for {emergency_request}",
        )
        log_event(
            "MANUAL_OVERRIDE",
            bed=row.healthcare_service_unit,
            patient=emergency_row.patient,
            inpatient_record=emergency_row.inpatient_record,
            allocation_request=row.allocation_request,
            previous_state=RESERVED,
            new_state="READY",
            reason=reason,
            correlation_id=reservation_name,
            details={
                "emergency_request": emergency_request,
                "clinical_approval_reference": approval,
                "reservation": reservation_name,
            },
            actor=actor,
        )
        log_event(
            "RESERVATION_CANCELLED",
            bed=row.healthcare_service_unit,
            allocation_request=row.allocation_request,
            inpatient_record=reservation.inpatient_record,
            previous_state=ACTIVE,
            new_state=CANCELLED,
            reason=f"Preempted for {emergency_request}",
            correlation_id=reservation_name,
            details={"clinical_approval_reference": approval},
            actor=actor,
        )
    return _result(reservation, idempotent=False)


def _window(start_at, expires_at, hold_hours):
    if not start_at:
        raise PostOperativeError(_("A surgery completion time is required."))
    try:
        if expires_at is None:
            return default_window(get_datetime(start_at), hold_hours=hold_hours)
        return ReservationWindow(get_datetime(start_at), get_datetime(expires_at))
    except WindowError as exc:
        raise PostOperativeError(str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise PostOperativeError(_("Invalid reservation window: {0}").format(exc)) from exc


def _reservation_by_key(key):
    names = frappe.get_all(
        "Bed Reservation",
        filters={"idempotency_key": key},
        pluck="name",
        limit_page_length=1,
    )
    return frappe.get_doc("Bed Reservation", names[0]) if names else None


def _idempotent_existing(existing, request_row, bed, window):
    if (
        existing.allocation_request != request_row.name
        or existing.healthcare_service_unit != bed
    ):
        raise ReservationConflict(
            _("Idempotency key {0} belongs to another reservation.").format(
                existing.idempotency_key
            )
        )
    if existing.status != ACTIVE:
        raise ReservationConflict(
            _("Idempotency key {0} was already used by {1} reservation {2}.").format(
                existing.idempotency_key, existing.status, existing.name
            )
        )
    if (
        get_datetime(existing.start_at) != window.start_at
        or get_datetime(existing.expires_at) != window.end_at
    ):
        raise ReservationConflict(
            _("Idempotency key {0} is already used for another time window.").format(
                existing.idempotency_key
            )
        )
    return _result(existing, idempotent=True)


def _assert_request_has_no_active_hold(allocation_request, exclude):
    filters = {"allocation_request": allocation_request, "status": ACTIVE}
    if exclude:
        filters["name"] = ("!=", exclude)
    existing = frappe.get_all(
        "Bed Reservation",
        filters=filters,
        pluck="name",
        limit_page_length=1,
    )
    if existing:
        raise ReservationConflict(
            _("Allocation Request {0} already has an active reservation {1}.").format(
                allocation_request, existing[0]
            )
        )


def _active_holds_for_bed(bed, exclude=None):
    params = [bed]
    exclusion = ""
    if exclude:
        exclusion = " AND name != %s"
        params.append(exclude)
    return frappe.db.sql(
        f"""
        SELECT name, start_at, expires_at, allocation_request
        FROM `tabBed Reservation`
        WHERE healthcare_service_unit = %s
          AND status = 'ACTIVE'
          {exclusion}
        ORDER BY name
        FOR UPDATE
        """,
        tuple(params),
        as_dict=True,
    )


def _assert_window_clear(rows, window, exclude):
    for row in rows:
        if row.name == exclude:
            continue
        try:
            overlaps = windows_overlap(
                window.start_at,
                window.end_at,
                get_datetime(row.start_at),
                get_datetime(row.expires_at),
            )
        except WindowError as exc:
            raise ReservationConflict(
                _("Existing reservation {0} has an invalid window.").format(row.name)
            ) from exc
        if overlaps:
            raise ReservationConflict(
                _("Bed already has an overlapping reservation {0}.").format(row.name)
            )


def _bed_context(bed):
    context = repository.get_bed_contexts([bed]).get(bed)
    if not context:
        raise ReservationError(_("Bed {0} does not exist.").format(bed))
    return context


def _assert_bed_ready(context):
    if not context.allocatable:
        raise ReservationConflict(
            _("Bed {0} is not allocatable.").format(context.name)
        )
    if context.occupied:
        raise ReservationConflict(
            _("Bed {0} is currently occupied.").format(context.name)
        )
    if context.state != "READY":
        raise ReservationConflict(
            _("Bed {0} is {1}; a post-operative hold requires a ready bed.").format(
                context.name, context.state
            )
        )
    elif context.active_reservation:
        raise ReservationConflict(
            _("Bed {0} already has an active reservation.").format(context.name)
        )


def _evaluate(allocation_request, context):
    request_context = repository.get_request_context(allocation_request)
    eligible_context = _replace_context(context)
    return evaluate(request_context, eligible_context, _care_level_map())


def _replace_context(context):
    from dataclasses import replace

    return replace(
        context,
        state="READY",
        active_reservation=False,
        occupied=False,
    )


def _care_level_map():
    policy = get_active_policy()
    raw = policy.eligibility_rules if policy else None
    if not raw:
        return None
    try:
        rules = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        frappe.throw(_("eligibility_rules is not valid JSON."))
    compatibility = rules.get("care_level_compatibility") if isinstance(rules, dict) else None
    if not compatibility:
        return None
    return {
        level: frozenset(values if isinstance(values, (list, tuple, set)) else [values])
        for level, values in compatibility.items()
    }


def _assert_post_operative(row):
    if row.reservation_type != POST_OPERATIVE:
        raise PostOperativeError(
            _("Reservation {0} is not a post-operative hold.").format(row.name)
        )


def _mark_confirmed(
    reservation, allocation_request, actor, correlation_id, reason, idempotent
):
    reservation.status = CONFIRMED
    reservation.save(ignore_permissions=True)
    _set_status("Bed Allocation Request", allocation_request, ALLOCATED)
    log_event(
        "RESERVATION_CONFIRMED",
        bed=reservation.healthcare_service_unit,
        inpatient_record=reservation.inpatient_record,
        allocation_request=allocation_request,
        new_state=CONFIRMED,
        reason=reason,
        correlation_id=correlation_id,
        details={"reservation": reservation.name, "idempotent": idempotent},
        actor=actor,
    )


def _release_bed_if_reserved(bed, actor, reason):
    if operational.get_state_value(bed) != RESERVED:
        return
    if frappe.db.exists(
        "Bed Reservation",
        {"healthcare_service_unit": bed, "status": ACTIVE},
    ):
        return
    operational.mark_ready(bed, reason=reason, actor=actor)
    log_event(
        "BED_RELEASED",
        bed=bed,
        previous_state=RESERVED,
        new_state="READY",
        reason=reason,
        actor=actor,
    )


def _set_status(doctype, name, status):
    frappe.db.set_value(doctype, name, "status", status, update_modified=True)


def _require_name(value, field):
    if not value or not isinstance(value, str):
        raise PostOperativeError(_("{0} is required.").format(field))
    return value.strip()


def _result(reservation, idempotent):
    return {
        "reservation": reservation.name,
        "request": reservation.allocation_request,
        "bed": reservation.healthcare_service_unit,
        "reservation_type": reservation.reservation_type,
        "status": reservation.status,
        "start_at": reservation.start_at,
        "expires_at": reservation.expires_at,
        "idempotent": idempotent,
    }
