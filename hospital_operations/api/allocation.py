"""Allocation confirmation and ADT endpoints.

All mutations are POST-only, role-checked on the server and idempotent where a
retry could otherwise create a second claim.
"""

import frappe
from frappe import _

from hospital_operations.api.common import (
    has_next,
    pagination,
    require_api_role,
    split_names,
)
from hospital_operations.bed_allocation import reservation
from hospital_operations.bed_allocation import run as allocation_run
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

ALLOWED_ROLES = (
    BED_MANAGER,
    ADMISSION_COORDINATOR,
    NURSING_SUPERVISOR,
    OPERATIONS_ADMIN,
)


@frappe.whitelist(methods=["POST"])
def confirm(proposal=None, idempotency_key=None, expected_state_version=None):
    """Confirm a proposal and admit the patient into its proposed bed."""

    _require_roles()
    if not proposal:
        frappe.throw("proposal is required.", frappe.ValidationError)
    return _run(
        reservation.confirm,
        proposal,
        idempotency_key=idempotency_key,
        expected_state_version=_optional_int(expected_state_version),
    )


@frappe.whitelist(methods=["POST"])
def cancel(proposal=None, reason=None):
    """Cancel an unconfirmed proposal and release its reservation."""

    _require_roles()
    if not proposal:
        frappe.throw("proposal is required.", frappe.ValidationError)
    return _run(reservation.cancel, proposal, reason=reason)


@frappe.whitelist(methods=["POST"])
def transfer(
    inpatient_record=None,
    to_bed=None,
    allocation_request=None,
    from_bed=None,
    idempotency_key=None,
):
    """Transfer an admitted patient to another eligible bed."""

    _require_roles()
    missing = [
        field
        for field, value in (
            ("inpatient_record", inpatient_record),
            ("to_bed", to_bed),
            ("allocation_request", allocation_request),
        )
        if not value
    ]
    if missing:
        frappe.throw(
            "Missing required fields: {0}.".format(", ".join(missing)),
            frappe.ValidationError,
        )
    return _run(
        reservation.transfer,
        inpatient_record,
        to_bed,
        allocation_request,
        from_bed=from_bed,
        idempotency_key=idempotency_key,
    )


@frappe.whitelist(methods=["POST"])
def discharge(inpatient_record=None, idempotency_key=None):
    """Discharge through Marley and move released beds to cleaning."""

    _require_roles()
    if not inpatient_record:
        frappe.throw("inpatient_record is required.", frappe.ValidationError)
    return _run(
        reservation.discharge,
        inpatient_record,
        idempotency_key=idempotency_key,
    )


@frappe.whitelist(methods=["POST"])
def optimize(request_names=None, policy=None, bed_names=None):
    """Run the operational allocation optimiser and persist proposals."""

    _require_roles()
    requests = split_names(request_names) or None
    beds = split_names(bed_names) or None
    run_doc = allocation_run.create_allocation_run(
        requests, policy_name=policy, bed_names=beds
    )
    proposals = _proposal_rows(run_doc.name, limit=200, start=0)
    return {
        "run": run_doc.name,
        "run_id": run_doc.run_id,
        "status": run_doc.status,
        "algorithm": run_doc.algorithm,
        "policy": run_doc.policy,
        "policy_version": run_doc.policy_version,
        "request_count": run_doc.request_count,
        "eligible_bed_count": run_doc.eligible_bed_count,
        "assigned_count": run_doc.assigned_count,
        "unassigned_count": run_doc.unassigned_count,
        "execution_duration_ms": run_doc.execution_duration_ms,
        "proposals": proposals,
    }


@frappe.whitelist(methods=["GET"])
def get_result(run=None, limit=None, start=None):
    """Return a sanitized allocation run and its paginated proposals."""

    _require_roles()
    if not run:
        frappe.throw(_("run is required."), frappe.ValidationError)
    limit, start = pagination(limit, start)
    run_doc = frappe.get_doc("Bed Allocation Run", run)
    total = frappe.db.count("Bed Allocation Proposal", {"allocation_run": run_doc.name})
    rows = _proposal_rows(run_doc.name, limit=limit, start=start)
    return {
        "run": {
            "name": run_doc.name,
            "run_id": run_doc.run_id,
            "status": run_doc.status,
            "algorithm": run_doc.algorithm,
            "started_at": run_doc.started_at,
            "completed_at": run_doc.completed_at,
            "execution_duration_ms": run_doc.execution_duration_ms,
            "policy": run_doc.policy,
            "policy_version": run_doc.policy_version,
            "input_hash": run_doc.input_hash,
            "request_count": run_doc.request_count,
            "eligible_bed_count": run_doc.eligible_bed_count,
            "assigned_count": run_doc.assigned_count,
            "unassigned_count": run_doc.unassigned_count,
        },
        "proposals": rows,
        "total": total,
        "limit": limit,
        "start": start,
        "has_next": has_next(start, len(rows), total),
    }


def _proposal_rows(run_name, limit, start):
    return frappe.get_all(
        "Bed Allocation Proposal",
        filters={"allocation_run": run_name},
        fields=[
            "name",
            "allocation_request",
            "proposed_bed",
            "status",
            "cost",
            "priority",
            "bed_state_version",
            "eligibility_summary",
            "explanation",
        ],
        order_by="name asc",
        limit_start=start,
        limit_page_length=limit,
    )


def _require_roles():
    require_api_role(*ALLOWED_ROLES)


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        frappe.throw(
            "expected_state_version must be an integer.", frappe.ValidationError
        )


def _run(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ReservationConflict as exc:
        frappe.throw(str(exc), ReservationConflict)
    except ReservationError as exc:
        frappe.throw(str(exc), ReservationError)
