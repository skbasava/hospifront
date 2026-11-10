"""Operational readiness service.

Marley occupancy stays authoritative. These helpers only move the additive
``Bed Operational State`` document and append audit events.

Phase 4 will add row locking and make claim confirmation atomic. Until then,
``expected_version`` provides optimistic stale-write protection.
"""

import frappe
from frappe import _

from hospital_operations.bed_allocation.state import (
    InvalidBedTransition,
    assert_transition,
)
from hospital_operations.utils.audit import log_event


def get_state(bed):
    if not frappe.db.exists("Bed Operational State", bed):
        return None
    return frappe.get_doc("Bed Operational State", bed)


def get_state_value(bed):
    doc = get_state(bed)
    return doc.state if doc else None


def ensure_state(bed, initial_state="READY"):
    existing = get_state(bed)
    if existing:
        return existing

    if not frappe.db.exists("Healthcare Service Unit", bed):
        frappe.throw(_("Healthcare Service Unit {0} does not exist.").format(bed))

    state = "OCCUPIED" if _is_occupied(bed) else initial_state
    doc = frappe.get_doc(
        {
            "doctype": "Bed Operational State",
            "healthcare_service_unit": bed,
            "state": state,
            "reason": "Initial operational state",
        }
    ).insert(ignore_permissions=True)
    log_event(
        "STATE_CHANGED",
        bed=bed,
        previous_state=None,
        new_state=state,
        reason="Initial operational state",
    )
    return doc


def set_state(bed, new_state, reason=None, expected_version=None, actor=None):
    doc = get_state(bed)
    if not doc:
        frappe.throw(
            _("Bed {0} has no operational state; call ensure_state first.").format(
                bed
            )
        )

    if expected_version is not None and int(doc.version or 0) != int(expected_version):
        frappe.throw(
            _("Bed {0} changed since it was proposed (expected version {1}).").format(
                bed, expected_version
            ),
            frappe.ValidationError,
        )

    previous = doc.state
    if previous == new_state:
        return doc

    try:
        assert_transition(previous, new_state)
    except InvalidBedTransition as exc:
        frappe.throw(str(exc))

    doc.state = new_state
    doc.reason = reason
    doc.save(ignore_permissions=True)
    log_event(
        "STATE_CHANGED",
        bed=bed,
        previous_state=previous,
        new_state=new_state,
        reason=reason,
        actor=actor,
    )
    return doc


def mark_reserved(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "RESERVED", reason, expected_version, actor)


def mark_occupied(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "OCCUPIED", reason, expected_version, actor)


def mark_vacated(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "VACATED", reason, expected_version, actor)


def require_cleaning(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "CLEANING_REQUIRED", reason, expected_version, actor)


def start_cleaning(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "CLEANING_IN_PROGRESS", reason, expected_version, actor)


def start_isolation_cleaning(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "ISOLATION_CLEANING", reason, expected_version, actor)


def mark_ready(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "READY", reason, expected_version, actor)


def block_bed(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "BLOCKED", reason, expected_version, actor)


def unblock_bed(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "READY", reason, expected_version, actor)


def mark_maintenance(bed, reason=None, expected_version=None, actor=None):
    return set_state(bed, "MAINTENANCE", reason, expected_version, actor)


def initialize_allocatable_states():
    beds = frappe.get_all(
        "Healthcare Service Unit",
        filters={"ho_is_allocatable": 1, "is_group": 0},
        pluck="name",
        limit_page_length=0,
    )
    created = []
    for bed in beds:
        if not frappe.db.exists("Bed Operational State", bed):
            ensure_state(bed)
            created.append(bed)
    return created


def _is_occupied(bed):
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM `tabInpatient Occupancy`
            WHERE parenttype = 'Inpatient Record'
              AND service_unit = %s
              AND check_out IS NULL
            LIMIT 1
            """,
            (bed,),
        )
    )
