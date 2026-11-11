"""Transaction-safe bed reservation, admission, transfer and discharge.

Clinical eligibility is checked again after every lock. The database's unique
active-immediate-claim index is the final guard; this module adds row locks,
idempotency, optimistic state-version checks and recoverable savepoints.
"""

import json
from dataclasses import replace

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from hospital_operations.bed_allocation import housekeeping, operational, repository
from hospital_operations.bed_allocation.eligibility import evaluate
from hospital_operations.bed_allocation.transaction import (
    ReservationConflict,
    ReservationError,
    StaleProposal,
    lock_bed_states,
    lock_beds,
    lock_document_values,
    lock_documents,
    lock_inpatient_record,
    savepoint,
)
from hospital_operations.integrations import marley
from hospital_operations.utils.audit import log_event
from hospital_operations.utils.config import get_active_policy

DEFAULT_RESERVATION_EXPIRY_MINUTES = 120
CONFIRMED = "CONFIRMED"
ACTIVE = "ACTIVE"
PROPOSED = "PROPOSED"
ALLOCATED = "ALLOCATED"
RESERVED = "RESERVED"
CANCELLED = "CANCELLED"
WAITLISTED = "WAITLISTED"


def confirm(proposal_name, idempotency_key=None, expected_state_version=None, actor=None):
    """Confirm a proposal and admit the patient into its bed atomically."""

    proposal_name = _require_name(proposal_name, "proposal")
    reservation_key = idempotency_key or f"confirm:{proposal_name}"
    actor = actor or frappe.session.user

    with savepoint("ho_confirm"):
        proposal_row = lock_document_values(
            "Bed Allocation Proposal",
            proposal_name,
            ["name", "allocation_request", "proposed_bed", "status"],
        )
        if proposal_row.status == CONFIRMED:
            return _confirmed_result(proposal_name, idempotent=True)

        request_name = proposal_row.allocation_request
        bed = proposal_row.proposed_bed
        request_row = lock_document_values(
            "Bed Allocation Request",
            request_name,
            ["name", "inpatient_record", "status", "patient"],
        )
        inpatient_record = request_row.inpatient_record
        if not inpatient_record:
            raise ReservationError(
                _("Allocation Request {0} has no Inpatient Record.").format(
                    request_name
                )
            )

        lock_inpatient_record(inpatient_record)
        lock_beds([bed])
        lock_bed_states([bed])

        proposal = _get_document("Bed Allocation Proposal", proposal_name)
        request = _get_document("Bed Allocation Request", request_name)
        if proposal.status == CONFIRMED:
            return _confirmed_result(proposal_name, idempotent=True)
        if proposal.status != PROPOSED:
            raise ReservationConflict(
                _("Proposal {0} is {1}, not {2}.").format(
                    proposal_name, proposal.status, PROPOSED
                )
            )
        if request.status not in (PROPOSED, RESERVED):
            raise ReservationConflict(
                _("Allocation Request {0} is {1}; confirmation requires {2} or {3}.").format(
                    request_name, request.status, PROPOSED, RESERVED
                )
            )

        existing = _find_reservation(reservation_key)
        if existing and existing.status == CONFIRMED:
            if (
                existing.allocation_request != request_name
                or existing.healthcare_service_unit != bed
            ):
                raise ReservationConflict(
                    _("Idempotency key {0} belongs to another confirmation.").format(
                        reservation_key
                    )
                )
            _mark_proposal_confirmed(proposal)
            return _confirmed_result(proposal_name, idempotent=True)

        resume = bool(existing and existing.status == ACTIVE)
        bed_context = _bed_context_for_confirmation(bed, resume)
        _assert_not_stale(proposal, bed_context, expected_state_version, resume)

        eligibility = _evaluate_request(request_name, bed_context, resume=resume)
        if not eligibility.eligible:
            details = "; ".join(reason.message for reason in eligibility.reasons)
            raise ReservationConflict(
                _("Bed {0} is no longer eligible: {1}").format(bed, details)
            )

        if not resume:
            if bed_context.state == "READY":
                existing = _create_reservation(
                    request_name,
                    bed,
                    inpatient_record,
                    reservation_key,
                    actor,
                )
                operational.mark_reserved(
                    bed,
                    reason=f"Reservation {existing.name} created",
                    expected_version=bed_context.state_version,
                    actor=actor,
                )
                _set_request_status(request_name, RESERVED)
                log_event(
                    "RESERVATION_CREATED",
                    bed=bed,
                    patient=request.patient,
                    inpatient_record=inpatient_record,
                    allocation_request=request_name,
                    new_state=RESERVED,
                    reason=f"Reservation {existing.name} created",
                    correlation_id=proposal_name,
                    details={"reservation": existing.name, "proposal": proposal_name},
                    actor=actor,
                )
            elif bed_context.state != "RESERVED":
                raise ReservationConflict(
                    _("Bed {0} is {1}; confirmation requires a ready bed.").format(
                        bed, bed_context.state
                    )
                )
            else:
                raise ReservationConflict(
                    _("Bed {0} is reserved without a matching reservation.").format(bed)
                )
        else:
            if existing.allocation_request != request_name or (
                existing.healthcare_service_unit != bed
            ):
                raise ReservationConflict(
                    _("Idempotency key {0} belongs to another reservation.").format(
                        reservation_key
                    )
                )
            if bed_context.state != "RESERVED":
                raise ReservationConflict(
                    _("Bed {0} is {1}; cannot resume the reservation.").format(
                        bed, bed_context.state
                    )
                )

        reservation = _get_document("Bed Reservation", existing.name)
        if reservation.status != ACTIVE:
            raise ReservationConflict(
                _("Reservation {0} is {1}, not active.").format(
                    reservation.name, reservation.status
                )
            )

        if not frappe.db.exists("Inpatient Record", inpatient_record):
            raise ReservationError(
                _("Inpatient Record {0} no longer exists.").format(inpatient_record)
            )
        marley.admit(inpatient_record, bed)
        operational.mark_occupied(
            bed,
            reason=f"Patient admitted under reservation {reservation.name}",
            actor=actor,
        )

        reservation.status = CONFIRMED
        reservation.save(ignore_permissions=True)
        _mark_proposal_confirmed(proposal)
        _set_request_status(request_name, ALLOCATED)
        log_event(
            "ADMISSION_LINKED",
            bed=bed,
            patient=request.patient,
            inpatient_record=inpatient_record,
            allocation_request=request_name,
            previous_state=RESERVED,
            new_state="OCCUPIED",
            reason="Marley admission completed",
            correlation_id=proposal_name,
            details={"reservation": reservation.name, "proposal": proposal_name},
            actor=actor,
        )
        log_event(
            "RESERVATION_CONFIRMED",
            bed=bed,
            patient=request.patient,
            inpatient_record=inpatient_record,
            allocation_request=request_name,
            new_state=CONFIRMED,
            reason="Reservation confirmed",
            correlation_id=proposal_name,
            details={"reservation": reservation.name, "proposal": proposal_name},
            actor=actor,
        )

    return _confirmed_result(proposal_name, idempotent=False)


def cancel(proposal_name, reason=None, actor=None):
    """Cancel an unconfirmed proposal and release any active reservation."""

    proposal_name = _require_name(proposal_name, "proposal")
    actor = actor or frappe.session.user

    with savepoint("ho_cancel"):
        proposal_row = lock_document_values(
            "Bed Allocation Proposal",
            proposal_name,
            ["name", "allocation_request", "proposed_bed", "status"],
        )
        if proposal_row.status == CANCELLED:
            return {"proposal": proposal_name, "status": CANCELLED, "idempotent": True}
        if proposal_row.status == CONFIRMED:
            raise ReservationConflict(
                _("Proposal {0} is confirmed; cancel the admission instead.").format(
                    proposal_name
                )
            )

        request_name = proposal_row.allocation_request
        bed = proposal_row.proposed_bed
        lock_document_values(
            "Bed Allocation Request", request_name, ["name", "inpatient_record", "status"]
        )
        lock_beds([bed])
        lock_bed_states([bed])
        proposal = _get_document("Bed Allocation Proposal", proposal_name)
        if proposal.status == CANCELLED:
            return {"proposal": proposal_name, "status": CANCELLED, "idempotent": True}
        if proposal.status != PROPOSED:
            raise ReservationConflict(
                _("Proposal {0} is {1}; only proposed allocations can be cancelled.").format(
                    proposal_name, proposal.status
                )
            )

        active = _active_reservation_for(request_name, bed)
        if active:
            _cancel_active_reservation(active, reason or "Allocation cancelled", actor)
        _release_bed_if_reserved(bed, actor, reason or "Allocation cancelled")

        _set_status("Bed Allocation Proposal", proposal_name, CANCELLED)
        _set_status("Bed Allocation Request", request_name, CANCELLED)
        log_event(
            "RESERVATION_CANCELLED",
            bed=bed,
            allocation_request=request_name,
            new_state=CANCELLED,
            reason=reason or "Allocation cancelled",
            correlation_id=proposal_name,
            actor=actor,
        )
    return {"proposal": proposal_name, "status": CANCELLED, "idempotent": False}


def transfer(
    inpatient_record,
    to_bed,
    allocation_request,
    from_bed=None,
    idempotency_key=None,
    actor=None,
):
    """Transfer an admitted patient, releasing the old bed for cleaning."""

    actor = actor or frappe.session.user
    inpatient_record = _require_name(inpatient_record, "inpatient_record")
    allocation_request = _require_name(allocation_request, "allocation_request")
    to_bed = _require_name(to_bed, "to_bed")

    with savepoint("ho_transfer"):
        request_row = lock_document_values(
            "Bed Allocation Request",
            allocation_request,
            ["name", "inpatient_record", "patient", "status"],
        )
        if request_row.inpatient_record != inpatient_record:
            raise ReservationError(
                _("Allocation Request {0} does not belong to {1}.").format(
                    allocation_request, inpatient_record
                )
            )
        lock_inpatient_record(inpatient_record)
        active_rows = marley.get_active_occupancies_for_update(inpatient_record)
        if not active_rows:
            raise ReservationConflict(
                _("Inpatient Record {0} has no active bed.").format(inpatient_record)
            )
        if len(active_rows) > 1:
            raise ReservationConflict(
                _("Inpatient Record {0} has multiple active beds.").format(
                    inpatient_record
                )
            )
        current = active_rows[0].service_unit
        if from_bed and from_bed != current:
            raise ReservationConflict(
                _("Inpatient Record {0} is in {1}, not {2}.").format(
                    inpatient_record, current, from_bed
                )
            )
        lock_beds([current, to_bed])
        lock_bed_states([current, to_bed])
        if marley.get_active_bed(inpatient_record) != current:
            raise ReservationConflict(
                _("Inpatient Record {0} changed bed while locking.").format(
                    inpatient_record
                )
            )
        request = _get_document("Bed Allocation Request", allocation_request)

        bed_context = _bed_context_for_confirmation(to_bed, resume=False)
        eligibility = _evaluate_request(allocation_request, bed_context, resume=False)
        if not eligibility.eligible:
            details = "; ".join(reason.message for reason in eligibility.reasons)
            raise ReservationConflict(
                _("Transfer bed {0} is not eligible: {1}").format(to_bed, details)
            )

        reservation = _create_reservation(
            allocation_request,
            to_bed,
            inpatient_record,
            idempotency_key or f"transfer:{inpatient_record}:{to_bed}",
            actor,
            reservation_type="TRANSFER",
        )
        operational.mark_reserved(
            to_bed,
            reason=f"Transfer reservation {reservation.name}",
            expected_version=bed_context.state_version,
            actor=actor,
        )
        _set_request_status(allocation_request, RESERVED)
        log_event(
            "TRANSFER_INITIATED",
            bed=current,
            patient=request.patient,
            inpatient_record=inpatient_record,
            allocation_request=allocation_request,
            previous_state="OCCUPIED",
            new_state="RESERVED",
            reason=f"Transfer to {to_bed}",
            correlation_id=reservation.name,
            details={"from_bed": current, "to_bed": to_bed},
            actor=actor,
        )

        marley.transfer(inpatient_record, to_bed, from_bed=current)

        operational.mark_vacated(
            current,
            reason=f"Transferred to {to_bed}",
            actor=actor,
        )
        operational.require_cleaning(
            current,
            reason=f"Cleaning required after transfer to {to_bed}",
            actor=actor,
        )
        housekeeping.ensure_task(
            current,
            "DISCHARGE_CLEAN",
            reason=f"Cleaning required after transfer to {to_bed}",
            actor=actor,
        )
        operational.mark_occupied(
            to_bed,
            reason=f"Transfer completed from {current}",
            actor=actor,
        )
        reservation.status = CONFIRMED
        reservation.save(ignore_permissions=True)
        _set_request_status(allocation_request, ALLOCATED)
        log_event(
            "TRANSFER_COMPLETED",
            bed=to_bed,
            patient=request.patient,
            inpatient_record=inpatient_record,
            allocation_request=allocation_request,
            previous_state=current,
            new_state=to_bed,
            reason="Marley transfer completed",
            correlation_id=reservation.name,
            details={"from_bed": current, "to_bed": to_bed},
            actor=actor,
        )
    return {
        "inpatient_record": inpatient_record,
        "from_bed": current,
        "to_bed": to_bed,
        "reservation": reservation.name,
        "status": CONFIRMED,
    }


def discharge(inpatient_record, idempotency_key=None, actor=None):
    """Discharge through Marley; released beds enter cleaning, never READY."""

    actor = actor or frappe.session.user
    inpatient_record = _require_name(inpatient_record, "inpatient_record")

    with savepoint("ho_discharge"):
        lock_inpatient_record(inpatient_record)
        active_rows = marley.get_active_occupancies_for_update(inpatient_record)
        active_beds = [row.service_unit for row in active_rows]
        lock_beds(active_beds)
        lock_bed_states(active_beds)
        record = _get_document("Inpatient Record", inpatient_record)
        if record.status == "Discharged" and not active_rows:
            return {
                "inpatient_record": inpatient_record,
                "released_beds": [],
                "status": "Discharged",
                "idempotent": True,
            }

        record, released = marley.discharge(inpatient_record)
        for bed in released:
            operational.mark_vacated(
                bed,
                reason=f"Discharge of {inpatient_record}",
                actor=actor,
            )
            operational.require_cleaning(
                bed,
                reason=f"Cleaning required after discharge of {inpatient_record}",
                actor=actor,
            )
            housekeeping.ensure_task(
                bed,
                "DISCHARGE_CLEAN",
                reason=f"Cleaning required after discharge of {inpatient_record}",
                actor=actor,
            )
            log_event(
                "BED_RELEASED",
                bed=bed,
                patient=record.patient,
                inpatient_record=inpatient_record,
                previous_state="OCCUPIED",
                new_state="CLEANING_REQUIRED",
                reason="Discharge completed",
                correlation_id=idempotency_key or inpatient_record,
                actor=actor,
            )
        log_event(
            "DISCHARGE_INITIATED",
            patient=record.patient,
            inpatient_record=inpatient_record,
            reason="Marley discharge completed",
            correlation_id=idempotency_key or inpatient_record,
            details={"released_beds": released},
            actor=actor,
        )
    return {
        "inpatient_record": inpatient_record,
        "released_beds": released,
        "status": record.status,
        "idempotent": False,
    }


def expire(now=None, actor=None):
    """Expire stale ACTIVE reservations and return their beds to READY."""

    now = now or now_datetime()
    actor = actor or "Administrator"
    rows = frappe.db.sql(
        """
        SELECT name, healthcare_service_unit, allocation_request
        FROM `tabBed Reservation`
        WHERE status = %s AND expires_at < %s
        ORDER BY name
        FOR UPDATE
        """,
        (ACTIVE, now),
        as_dict=True,
    )
    expired = 0
    for row in rows:
        with savepoint(f"ho_expire_{row.name}"):
            lock_documents("Bed Reservation", [row.name])
            lock_beds([row.healthcare_service_unit])
            lock_bed_states([row.healthcare_service_unit])
            reservation = _get_document("Bed Reservation", row.name)
            if reservation.status != ACTIVE:
                continue
            reservation.status = "EXPIRED"
            reservation.save(ignore_permissions=True)
            _release_bed_if_reserved(
                reservation.healthcare_service_unit,
                actor,
                f"Reservation {reservation.name} expired",
            )
            if reservation.allocation_request:
                _set_request_status(reservation.allocation_request, WAITLISTED)
            log_event(
                "RESERVATION_EXPIRED",
                bed=reservation.healthcare_service_unit,
                allocation_request=reservation.allocation_request,
                inpatient_record=reservation.inpatient_record,
                new_state="EXPIRED",
                reason="Reservation window expired",
                correlation_id=reservation.name,
                actor=actor,
            )
            expired += 1
    return {"expired": expired}


def reconcile(actor=None):
    """Recover orphan reservations and RESERVED beds left without a claim."""

    actor = actor or "Administrator"
    orphaned = frappe.db.sql(
        """
        SELECT br.name, br.healthcare_service_unit, br.allocation_request
        FROM `tabBed Reservation` br
        LEFT JOIN `tabBed Allocation Request` bar ON bar.name = br.allocation_request
        WHERE br.status = 'ACTIVE'
          AND (bar.name IS NULL OR bar.status = 'CANCELLED')
        ORDER BY br.name
        FOR UPDATE
        """,
        as_dict=True,
    )
    reconciled = 0
    for row in orphaned:
        with savepoint(f"ho_reconcile_{row.name}"):
            lock_documents("Bed Reservation", [row.name])
            lock_beds([row.healthcare_service_unit])
            lock_bed_states([row.healthcare_service_unit])
            reservation = _get_document("Bed Reservation", row.name)
            if reservation.status != ACTIVE:
                continue
            _cancel_active_reservation(
                reservation.name, "Reconciled orphan reservation", actor
            )
            _release_bed_if_reserved(
                reservation.healthcare_service_unit, actor, "Reconciled orphan reservation"
            )
            reconciled += 1

    released = 0
    reserved_states = frappe.db.sql(
        """
        SELECT name, healthcare_service_unit
        FROM `tabBed Operational State`
        WHERE state = 'RESERVED'
        ORDER BY healthcare_service_unit
        FOR UPDATE
        """,
        as_dict=True,
    )
    for row in reserved_states:
        bed = row.healthcare_service_unit
        if frappe.db.exists(
            "Bed Reservation",
            {"healthcare_service_unit": bed, "status": ACTIVE},
        ):
            continue
        with savepoint(f"ho_release_{bed}"):
            lock_beds([bed])
            lock_bed_states([bed])
            if operational.get_state_value(bed) != "RESERVED":
                continue
            if frappe.db.exists(
                "Bed Reservation",
                {"healthcare_service_unit": bed, "status": ACTIVE},
            ):
                continue
            operational.mark_ready(
                bed,
                reason="Reconciled reserved state without an active reservation",
                actor=actor,
            )
            log_event(
                "BED_RELEASED",
                bed=bed,
                previous_state="RESERVED",
                new_state="READY",
                reason="Reservation reconciliation",
                actor=actor,
            )
            released += 1
    return {"reconciled_reservations": reconciled, "released_beds": released}


def _confirmed_result(proposal_name, idempotent):
    proposal = _get_document("Bed Allocation Proposal", proposal_name)
    return {
        "proposal": proposal.name,
        "request": proposal.allocation_request,
        "bed": proposal.proposed_bed,
        "status": proposal.status,
        "idempotent": idempotent,
    }


def _require_name(value, field):
    if not value or not isinstance(value, str):
        raise ReservationError(_("{0} is required.").format(field))
    return value.strip()


def _get_document(doctype, name):
    if not frappe.db.exists(doctype, name):
        raise ReservationError(_("{0} {1} does not exist.").format(doctype, name))
    return frappe.get_doc(doctype, name)


def _find_reservation(idempotency_key):
    if not idempotency_key:
        return None
    names = frappe.get_all(
        "Bed Reservation",
        filters={"idempotency_key": idempotency_key},
        pluck="name",
        limit_page_length=1,
    )
    return _get_document("Bed Reservation", names[0]) if names else None


def _active_reservation_for(request_name, bed):
    names = frappe.get_all(
        "Bed Reservation",
        filters={
            "allocation_request": request_name,
            "healthcare_service_unit": bed,
            "status": ACTIVE,
        },
        pluck="name",
        limit_page_length=1,
    )
    return names[0] if names else None


def _create_reservation(
    request_name,
    bed,
    inpatient_record,
    idempotency_key,
    actor,
    reservation_type="IMMEDIATE",
):
    policy = get_active_policy()
    minutes = int(
        (policy.reservation_expiry if policy else 0)
        or DEFAULT_RESERVATION_EXPIRY_MINUTES
    )
    start_at = now_datetime()
    reservation = frappe.get_doc(
        {
            "doctype": "Bed Reservation",
            "allocation_request": request_name,
            "healthcare_service_unit": bed,
            "reservation_type": reservation_type,
            "status": ACTIVE,
            "start_at": start_at,
            "expires_at": add_to_date(start_at, minutes=minutes),
            "idempotency_key": idempotency_key,
            "inpatient_record": inpatient_record,
            "reason": f"{reservation_type} reservation",
            "created_by": actor,
        }
    ).insert(ignore_permissions=True)
    return reservation


def _bed_context_for_confirmation(bed, resume):
    operational.ensure_state(bed)
    context = repository.get_bed_contexts([bed]).get(bed)
    if not context:
        raise ReservationError(_("Bed {0} does not exist.").format(bed))
    if resume:
        return replace(context, active_reservation=False)
    return context


def _evaluate_request(request_name, bed_context, resume):
    context = repository.get_request_context(request_name)
    if resume:
        bed_context = replace(
            bed_context, state="READY", active_reservation=False, occupied=False
        )
    return evaluate(context, bed_context, _care_level_map())


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


def _assert_not_stale(proposal, bed_context, expected_state_version, resume):
    if resume:
        return
    expected = (
        expected_state_version
        if expected_state_version is not None
        else proposal.bed_state_version
    )
    if expected is None:
        return
    current = int(bed_context.state_version or 0)
    if current != int(expected):
        raise StaleProposal(
            _(
                "Proposal {0} is stale: bed {1} state version is {2}, expected {3}."
            ).format(proposal.name, bed_context.name, current, expected)
        )


def _mark_proposal_confirmed(proposal):
    if proposal.status != CONFIRMED:
        proposal.status = CONFIRMED
        proposal.save(ignore_permissions=True)


def _set_request_status(request_name, status):
    _set_status("Bed Allocation Request", request_name, status)


def _set_status(doctype, name, status):
    frappe.db.set_value(doctype, name, "status", status, update_modified=True)


def _cancel_active_reservation(reservation_name, reason, actor):
    reservation = _get_document("Bed Reservation", reservation_name)
    if reservation.status != ACTIVE:
        return
    reservation.status = CANCELLED
    reservation.reason = reason
    reservation.save(ignore_permissions=True)
    log_event(
        "RESERVATION_CANCELLED",
        bed=reservation.healthcare_service_unit,
        allocation_request=reservation.allocation_request,
        inpatient_record=reservation.inpatient_record,
        previous_state=ACTIVE,
        new_state=CANCELLED,
        reason=reason,
        correlation_id=reservation.name,
        actor=actor,
    )


def _release_bed_if_reserved(bed, actor, reason):
    if operational.get_state_value(bed) != "RESERVED":
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
        previous_state="RESERVED",
        new_state="READY",
        reason=reason,
        actor=actor,
    )
