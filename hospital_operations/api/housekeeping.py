"""Housekeeping lifecycle endpoints."""

import frappe

from hospital_operations.api.common import require_api_role, require_value
from hospital_operations.bed_allocation import housekeeping
from hospital_operations.utils.roles import (
    BED_MANAGER,
    HOUSEKEEPING_STAFF,
    NURSING_SUPERVISOR,
    OPERATIONS_ADMIN,
)

ALLOWED_ROLES = (
    HOUSEKEEPING_STAFF,
    BED_MANAGER,
    NURSING_SUPERVISOR,
    OPERATIONS_ADMIN,
)


@frappe.whitelist(methods=["POST"])
def start(task=None, assigned_employee=None, reason=None):
    """Start a pending cleaning task."""

    require_api_role(*ALLOWED_ROLES)
    require_value(task, "task")
    doc = housekeeping.start_task(
        task, assigned_employee=assigned_employee, reason=reason
    )
    return _serialize(doc)


@frappe.whitelist(methods=["POST"])
def complete(task=None, reason=None):
    """Mark cleaning complete without releasing the bed."""

    require_api_role(*ALLOWED_ROLES)
    require_value(task, "task")
    return _serialize(housekeeping.complete_task(task, reason=reason))


@frappe.whitelist(methods=["POST"])
def verify(task=None, verified_by=None, reason=None):
    """Verify cleaning and release the bed to READY."""

    require_api_role(*ALLOWED_ROLES)
    require_value(task, "task")
    return _serialize(housekeeping.verify_task(task, verified_by, reason))


@frappe.whitelist(methods=["POST"])
def cancel(task=None, reason=None):
    """Cancel an unverified cleaning task."""

    require_api_role(*ALLOWED_ROLES)
    require_value(task, "task")
    return _serialize(housekeeping.cancel_task(task, reason=reason))


def _serialize(doc):
    return {
        "name": doc.name,
        "bed": doc.healthcare_service_unit,
        "task_type": doc.task_type,
        "status": doc.status,
        "assigned_employee": doc.assigned_employee,
        "started_at": doc.started_at,
        "completed_at": doc.completed_at,
        "verified_by": doc.verified_by,
        "verified_at": doc.verified_at,
    }
