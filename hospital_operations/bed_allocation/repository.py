"""Frappe adapters that build pure eligibility contexts.

Queries are batched to avoid per-bed round trips. Marley's
``Inpatient Occupancy`` remains the authoritative occupancy signal; this module
does not write occupancy.
"""

import frappe
from frappe.utils import get_datetime, now_datetime

from hospital_operations.bed_allocation import staffing_service
from hospital_operations.bed_allocation.eligibility import (
    BedContext,
    ClinicalRequirement,
    RequestContext,
    evaluate,
)


def get_request_context(request_name):
    doc = frappe.get_doc("Bed Allocation Request", request_name)
    requirements = tuple(
        ClinicalRequirement(
            requirement_type=row.requirement_type,
            required_value=row.required_value or "",
            operator=row.operator or "=",
            mandatory=bool(row.mandatory),
            clinical_reason=row.clinical_reason or "",
        )
        for row in doc.clinical_requirements or []
    )
    return RequestContext(
        name=doc.name,
        care_level=doc.care_level,
        priority=doc.priority,
        medical_department=doc.medical_department,
        requirements=requirements,
        requested_at=str(doc.requested_at) if doc.requested_at else None,
        source=doc.source or "Direct Admission",
    )


def get_bed_contexts(bed_names):
    names = list(dict.fromkeys(name for name in bed_names if name))
    if not names:
        return {}

    units = frappe.get_all(
        "Healthcare Service Unit",
        filters={"name": ("in", names)},
        fields=[
            "name",
            "is_group",
            "parent_healthcare_service_unit",
            "ho_ward",
            "ho_is_allocatable",
            "ho_care_level",
        ],
        limit_page_length=0,
    )
    states = _load_states(names)
    capabilities, specialties = _load_capabilities(names)
    occupied = _load_occupied(names)
    reserved = _load_active_reservations(names)
    staffing = _load_staffing(
        {
            row.get("ho_ward") or row.get("parent_healthcare_service_unit")
            for row in units
        }
        - {None}
    )

    contexts = {}
    for row in units:
        name = row.name
        ward = row.get("ho_ward") or row.get("parent_healthcare_service_unit")
        state_row = states.get(name)
        contexts[name] = BedContext(
            name=name,
            care_level=row.get("ho_care_level") or "Other",
            state=state_row.state if state_row else "UNKNOWN",
            allocatable=bool(row.get("ho_is_allocatable")),
            occupied=name in occupied,
            active_reservation=name in reserved,
            capabilities=capabilities.get(name, {}),
            specialties=frozenset(specialties.get(name, ())),
            ward=ward,
            room_sharing_ok=True,
            staffed_capacity_available=staffing.get(ward),
            state_version=state_row.version if state_row else None,
        )
    return contexts


def evaluate_request(request_name, bed_names=None):
    request = get_request_context(request_name)
    if bed_names is None:
        bed_names = frappe.get_all(
            "Healthcare Service Unit",
            filters={"ho_is_allocatable": 1, "is_group": 0},
            pluck="name",
            limit_page_length=0,
        )
    contexts = get_bed_contexts(bed_names)
    results = [evaluate(request, contexts[name]) for name in sorted(contexts)]
    return results


def eligible_bed_names(request_name, bed_names=None):
    return [
        result.bed
        for result in evaluate_request(request_name, bed_names)
        if result.eligible
    ]


def _load_states(names):
    rows = frappe.get_all(
        "Bed Operational State",
        filters={"healthcare_service_unit": ("in", names)},
        fields=["healthcare_service_unit", "state", "version"],
        limit_page_length=0,
    )
    return {row.healthcare_service_unit: row for row in rows}


def _load_capabilities(names):
    rows = frappe.get_all(
        "Bed Capability",
        filters={"healthcare_service_unit": ("in", names), "enabled": 1},
        fields=[
            "healthcare_service_unit",
            "capability_type",
            "capability_value",
            "valid_from",
            "valid_until",
        ],
        limit_page_length=0,
    )
    current = now_datetime()
    capabilities = {}
    specialties = {}
    for row in rows:
        if row.valid_from and get_datetime(row.valid_from) > current:
            continue
        if row.valid_until and get_datetime(row.valid_until) <= current:
            continue
        bed_caps = capabilities.setdefault(row.healthcare_service_unit, {})
        values = bed_caps.setdefault(row.capability_type, set())
        if row.capability_value:
            values.add(row.capability_value)
        if row.capability_type == "SPECIALTY" and row.capability_value:
            specialties.setdefault(row.healthcare_service_unit, set()).add(
                row.capability_value
            )
    frozen = {
        bed: {cap_type: frozenset(values) for cap_type, values in caps.items()}
        for bed, caps in capabilities.items()
    }
    return frozen, specialties


def _load_occupied(names):
    placeholders = ", ".join(["%s"] * len(names))
    rows = frappe.db.sql(
        f"""
        SELECT io.service_unit AS bed
        FROM `tabInpatient Occupancy` io
        WHERE io.parenttype = 'Inpatient Record'
          AND io.service_unit IN ({placeholders})
          AND io.check_out IS NULL
        GROUP BY io.service_unit
        """,
        tuple(names),
        as_dict=True,
    )
    return {row.bed for row in rows}


def _load_active_reservations(names):
    rows = frappe.get_all(
        "Bed Reservation",
        filters={"healthcare_service_unit": ("in", names), "status": "ACTIVE"},
        pluck="healthcare_service_unit",
        limit_page_length=0,
    )
    return set(rows)


def _load_staffing(wards):
    """Return live ``ward -> bool | None`` staffing availability.

    ``None`` means the active policy has no staffing configuration, so the
    constraint is deliberately not enforced. The snapshot DocType remains a
    historical report, not the live admission gate.
    """

    if not wards:
        return {}
    return staffing_service.staffing_availability(wards)
