"""Staffed-capacity read endpoints."""

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from hospital_operations.api.common import require_api_role, split_names
from hospital_operations.bed_allocation import staffing_service
from hospital_operations.utils.roles import (
    BED_MANAGER,
    NURSING_SUPERVISOR,
    OPERATIONS_ADMIN,
)

ALLOWED_ROLES = (BED_MANAGER, NURSING_SUPERVISOR, OPERATIONS_ADMIN)


@frappe.whitelist(methods=["GET"])
def capacity(ward=None, wards=None, at=None):
    """Return current staffed capacity for one or more wards."""

    require_api_role(*ALLOWED_ROLES)
    at = get_datetime(at) if at else now_datetime()
    ward_names = split_names(wards)
    if ward:
        ward_names = [ward]
    if not ward_names:
        ward_names = _allocatable_wards()
    if not ward_names:
        return {"at": str(at), "policy_version": None, "wards": {}}

    results = {}
    policy_version = None
    for ward_name in ward_names:
        result = staffing_service.ward_capacity(ward_name, at=at)
        results[ward_name] = result.as_dict()
        policy_version = policy_version or result.policy_version
    return {
        "at": str(at),
        "policy_version": policy_version,
        "wards": results,
    }


def _allocatable_wards():
    rows = frappe.get_all(
        "Healthcare Service Unit",
        filters={"ho_is_allocatable": 1, "is_group": 0},
        fields=["ho_ward", "parent_healthcare_service_unit"],
        limit_page_length=0,
    )
    wards = {
        row.ho_ward or row.parent_healthcare_service_unit
        for row in rows
        if row.ho_ward or row.parent_healthcare_service_unit
    }
    if not wards:
        frappe.throw(_("No allocatable ward found."), frappe.ValidationError)
    return sorted(wards)
