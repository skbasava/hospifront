"""Bed cleaning lifecycle and the READY release gate.

A bed that leaves occupancy must never become READY until a housekeeping task
is verified. Tasks are append-only history: each cleaning cycle gets its own
task, while only one task per cycle may be open.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from hospital_operations.bed_allocation import operational
from hospital_operations.bed_allocation.state import CLEANING_STATES
from hospital_operations.bed_allocation.transaction import (
    lock_bed_states,
    lock_beds,
    lock_document_values,
    savepoint,
)
from hospital_operations.utils.audit import log_event

OPEN_STATUSES = ("PENDING", "IN_PROGRESS", "COMPLETED")
TASK_TYPES = (
    "DISCHARGE_CLEAN",
    "ISOLATION_CLEAN",
    "DEEP_CLEAN",
    "MAINTENANCE_CLEAN",
    "OTHER",
)


def ensure_task(bed, task_type=None, reason=None, notes=None, actor=None):
    """Return an open cleaning task for the bed, creating one if needed."""

    bed = _require_name(bed, "bed")
    with savepoint("ho_housekeeping_create"):
        lock_beds([bed])
        lock_bed_states([bed])
        state = operational.get_state_value(bed)
        existing = _open_task(bed)
        if existing:
            return existing
        if state not in CLEANING_STATES and state != "VACATED":
            frappe.throw(
                _("Bed {0} is not awaiting cleaning (state {1}).").format(
                    bed, state or "unknown"
                )
            )
        task_type = task_type or _task_type_for_state(state)
        if task_type not in TASK_TYPES:
            frappe.throw(_("Unknown housekeeping task type: {0}").format(task_type))
        doc = frappe.get_doc(
            {
                "doctype": "Housekeeping Task",
                "healthcare_service_unit": bed,
                "task_type": task_type,
                "status": "PENDING",
                "notes": notes,
            }
        ).insert(ignore_permissions=True)
        log_event(
            "STATE_CHANGED",
            bed=bed,
            previous_state=state,
            new_state="HOUSEKEEPING_PENDING",
            reason=reason or "Cleaning task created",
            details={"housekeeping_task": doc.name, "task_type": task_type},
            actor=actor,
        )
    return doc


def start_task(task, assigned_employee=None, reason=None, actor=None):
    """Move an open task to IN_PROGRESS and start the bed cleaning state."""

    task_name = _require_name(task, "task")
    with savepoint("ho_housekeeping_start"):
        row = lock_document_values(
            "Housekeeping Task",
            task_name,
            ["name", "status", "healthcare_service_unit", "task_type"],
        )
        bed = row.healthcare_service_unit
        lock_beds([bed])
        lock_bed_states([bed])
        doc = frappe.get_doc("Housekeeping Task", task_name)
        if doc.status == "IN_PROGRESS":
            return doc
        if doc.status != "PENDING":
            frappe.throw(
                _("Housekeeping Task {0} cannot start from status {1}.").format(
                    task_name, doc.status
                )
            )
        state = operational.get_state_value(bed)
        target = (
            "ISOLATION_CLEANING"
            if doc.task_type == "ISOLATION_CLEAN" or state == "ISOLATION_CLEANING"
            else "CLEANING_IN_PROGRESS"
        )
        if state != target:
            if state not in CLEANING_STATES and state != "VACATED":
                frappe.throw(
                    _("Bed {0} is not awaiting cleaning (state {1}).").format(
                        bed, state or "unknown"
                    )
                )
            operational.set_state(
                bed, target, reason=reason or "Housekeeping started", actor=actor
            )
        doc.status = "IN_PROGRESS"
        if assigned_employee:
            doc.assigned_employee = assigned_employee
        if not doc.started_at:
            doc.started_at = now_datetime()
        doc.save(ignore_permissions=True)
        log_event(
            "HOUSEKEEPING_STARTED",
            bed=bed,
            previous_state=state,
            new_state=target,
            reason=reason or "Housekeeping started",
            details={"housekeeping_task": doc.name, "task_type": doc.task_type},
            actor=actor,
        )
    return doc


def complete_task(task, reason=None, actor=None):
    """Mark cleaning complete; the bed remains unavailable until verification."""

    task_name = _require_name(task, "task")
    with savepoint("ho_housekeeping_complete"):
        row = lock_document_values(
            "Housekeeping Task",
            task_name,
            ["name", "status", "healthcare_service_unit"],
        )
        bed = row.healthcare_service_unit
        lock_beds([bed])
        lock_bed_states([bed])
        doc = frappe.get_doc("Housekeeping Task", task_name)
        if doc.status == "COMPLETED":
            return doc
        if doc.status != "IN_PROGRESS":
            frappe.throw(
                _("Housekeeping Task {0} cannot complete from status {1}.").format(
                    task_name, doc.status
                )
            )
        doc.status = "COMPLETED"
        doc.save(ignore_permissions=True)
        log_event(
            "HOUSEKEEPING_COMPLETED",
            bed=bed,
            previous_state=operational.get_state_value(bed),
            new_state=operational.get_state_value(bed),
            reason=reason or "Housekeeping completed",
            details={"housekeeping_task": doc.name},
            actor=actor,
        )
    return doc


def verify_task(task, verified_by=None, reason=None, actor=None):
    """Verify cleaning and release the bed to READY."""

    task_name = _require_name(task, "task")
    with savepoint("ho_housekeeping_verify"):
        row = lock_document_values(
            "Housekeeping Task",
            task_name,
            ["name", "status", "healthcare_service_unit"],
        )
        bed = row.healthcare_service_unit
        lock_beds([bed])
        lock_bed_states([bed])
        doc = frappe.get_doc("Housekeeping Task", task_name)
        if doc.status == "VERIFIED":
            return doc
        if doc.status != "COMPLETED":
            frappe.throw(
                _("Housekeeping Task {0} cannot be verified from status {1}.").format(
                    task_name, doc.status
                )
            )
        state = operational.get_state_value(bed)
        doc.status = "VERIFIED"
        doc.verified_by = verified_by or actor or frappe.session.user
        doc.save(ignore_permissions=True)
        if state in CLEANING_STATES:
            operational.mark_ready(
                bed, reason=reason or "Housekeeping verified", actor=actor
            )
            log_event(
                "BED_READY",
                bed=bed,
                previous_state=state,
                new_state="READY",
                reason=reason or "Housekeeping verified",
                details={"housekeeping_task": doc.name},
                actor=actor,
            )
    return doc


def cancel_task(task, reason=None, actor=None):
    """Cancel a task without changing the bed's cleaning state."""

    task_name = _require_name(task, "task")
    with savepoint("ho_housekeeping_cancel"):
        row = lock_document_values(
            "Housekeeping Task",
            task_name,
            ["name", "status", "healthcare_service_unit"],
        )
        bed = row.healthcare_service_unit
        lock_beds([bed])
        lock_bed_states([bed])
        doc = frappe.get_doc("Housekeeping Task", task_name)
        if doc.status == "CANCELLED":
            return doc
        if doc.status == "VERIFIED":
            frappe.throw(
                _("Verified Housekeeping Task {0} cannot be cancelled.").format(
                    task_name
                )
            )
        doc.status = "CANCELLED"
        doc.save(ignore_permissions=True)
        log_event(
            "STATE_CHANGED",
            bed=bed,
            previous_state=operational.get_state_value(bed),
            new_state="HOUSEKEEPING_CANCELLED",
            reason=reason or "Housekeeping task cancelled",
            details={"housekeeping_task": doc.name},
            actor=actor,
        )
    return doc


def reconcile_cleaning_tasks(actor=None):
    """Create tasks for cleaning beds that have no open task."""

    beds = frappe.get_all(
        "Bed Operational State",
        filters={"state": ("in", list(CLEANING_STATES))},
        pluck="healthcare_service_unit",
        limit_page_length=0,
    )
    created = []
    tasks = []
    for bed in sorted(beds):
        if _open_task(bed):
            continue
        task = ensure_task(
            bed, reason="Cleaning task reconciled", actor=actor
        )
        created.append(bed)
        tasks.append(task.name)
    return {"created": created, "tasks": tasks, "checked": len(beds)}


def latest_task(bed):
    names = frappe.get_all(
        "Housekeeping Task",
        filters={"healthcare_service_unit": bed},
        pluck="name",
        order_by="creation desc, name desc",
        limit_page_length=1,
    )
    return frappe.get_doc("Housekeeping Task", names[0]) if names else None


def has_verified_cleaning(bed):
    task = latest_task(bed)
    return bool(task and task.status == "VERIFIED")


def _open_task(bed):
    names = frappe.get_all(
        "Housekeeping Task",
        filters={
            "healthcare_service_unit": bed,
            "status": ("in", list(OPEN_STATUSES)),
        },
        pluck="name",
        order_by="creation desc, name desc",
        limit_page_length=1,
    )
    return frappe.get_doc("Housekeeping Task", names[0]) if names else None


def _task_type_for_state(state):
    if state == "ISOLATION_CLEANING":
        return "ISOLATION_CLEAN"
    return "DISCHARGE_CLEAN"


def _require_name(value, fieldname):
    if hasattr(value, "name"):
        value = value.name
    if not value:
        frappe.throw(_("{0} is required.").format(fieldname.replace("_", " ").title()))
    return value
