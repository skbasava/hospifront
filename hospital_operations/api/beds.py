"""Read-only bed registry and availability endpoints."""

import frappe

from hospital_operations.api.common import (
    as_bool,
    has_next,
    pagination,
    require_api_role,
    split_names,
)
from hospital_operations.bed_allocation import repository
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


@frappe.whitelist(methods=["GET"])
def list(
    ward=None,
    care_level=None,
    state=None,
    allocatable=None,
    limit=None,
    start=None,
):
    """List beds with their current operational projection."""

    require_api_role(*ALLOWED_ROLES)
    limit, start = pagination(limit, start)

    filters = {"is_group": 0}
    if ward:
        filters["ho_ward"] = ward
    if care_level:
        filters["ho_care_level"] = care_level
    if allocatable is not None:
        filters["ho_is_allocatable"] = 1 if as_bool(allocatable) else 0

    states = split_names(state)
    if states:
        names = frappe.get_all(
            "Bed Operational State",
            filters={"state": ("in", states)},
            pluck="healthcare_service_unit",
            limit_page_length=0,
        )
        if not names:
            return _page([], 0, limit, start)
        filters["name"] = ("in", names)

    total = frappe.db.count("Healthcare Service Unit", filters)
    units = frappe.get_all(
        "Healthcare Service Unit",
        filters=filters,
        fields=[
            "name",
            "healthcare_service_unit_name",
            "ho_ward",
            "ho_care_level",
            "ho_is_allocatable",
            "parent_healthcare_service_unit",
        ],
        order_by="name asc",
        limit_start=start,
        limit_page_length=limit,
    )
    rows = _project(units)
    response = _page(rows, total, limit, start)
    response["beds"] = response.pop("data")
    return response


@frappe.whitelist(methods=["GET"])
def availability(request=None, ward=None, care_level=None, limit=None, start=None):
    """Return ready beds, or eligibility results for one allocation request."""

    require_api_role(*ALLOWED_ROLES)
    limit, start = pagination(limit, start)

    filters = {"is_group": 0, "ho_is_allocatable": 1}
    if ward:
        filters["ho_ward"] = ward
    if care_level:
        filters["ho_care_level"] = care_level

    bed_names = frappe.get_all(
        "Healthcare Service Unit",
        filters=filters,
        pluck="name",
        order_by="name asc",
        limit_page_length=0,
    )
    if request:
        results = repository.evaluate_request(request, bed_names)
        total = len(results)
        rows = [result.as_dict() for result in results[start : start + limit]]
        eligible_count = sum(1 for result in results if result.eligible)
        return {
            "request": request,
            "beds": rows,
            "eligible_count": eligible_count,
            "total": total,
            "limit": limit,
            "start": start,
            "has_next": has_next(start, len(rows), total),
        }

    contexts = repository.get_bed_contexts(bed_names)
    ready = [
        context
        for name, context in sorted(contexts.items())
        if context.allocatable
        and context.state == "READY"
        and not context.occupied
        and not context.active_reservation
    ]
    total = len(ready)
    rows = [_context_row(context) for context in ready[start : start + limit]]
    return {
        "beds": rows,
        "total": total,
        "limit": limit,
        "start": start,
        "has_next": has_next(start, len(rows), total),
    }


def _project(units):
    names = [row.name for row in units]
    if not names:
        return []
    states = _state_map(names)
    occupied = _occupied_map(names)
    reservations = _reservation_map(names)
    rows = []
    for unit in units:
        state = states.get(unit.name, {})
        rows.append(
            {
                "name": unit.name,
                "healthcare_service_unit_name": unit.healthcare_service_unit_name,
                "ward": unit.ho_ward or unit.parent_healthcare_service_unit,
                "care_level": unit.ho_care_level,
                "allocatable": bool(unit.ho_is_allocatable),
                "state": state.get("state"),
                "state_version": state.get("version") or 0,
                "occupied": unit.name in occupied,
                "active_reservation": unit.name in reservations,
            }
        )
    return rows


def _context_row(context):
    return {
        "name": context.name,
        "ward": context.ward,
        "care_level": context.care_level,
        "state": context.state,
        "state_version": context.state_version,
        "allocatable": context.allocatable,
        "occupied": context.occupied,
        "active_reservation": context.active_reservation,
        "staffed_capacity_available": context.staffed_capacity_available,
    }


def _state_map(names):
    rows = frappe.get_all(
        "Bed Operational State",
        filters={"healthcare_service_unit": ("in", names)},
        fields=["healthcare_service_unit", "state", "version"],
        limit_page_length=0,
    )
    return {row.healthcare_service_unit: row for row in rows}


def _occupied_map(names):
    if not names:
        return set()
    rows = frappe.db.sql(
        """
        SELECT DISTINCT io.service_unit
        FROM `tabInpatient Occupancy` io
        WHERE io.service_unit IN %(names)s
          AND io.check_out IS NULL
        """,
        {"names": tuple(names)},
        as_dict=True,
    )
    return {row.service_unit for row in rows}


def _reservation_map(names):
    rows = frappe.get_all(
        "Bed Reservation",
        filters={
            "healthcare_service_unit": ("in", names),
            "status": "ACTIVE",
        },
        pluck="healthcare_service_unit",
        limit_page_length=0,
    )
    return set(rows)


def _page(rows, total, limit, start):
    return {
        "data": rows,
        "total": total,
        "limit": limit,
        "start": start,
        "has_next": has_next(start, len(rows), total),
    }
