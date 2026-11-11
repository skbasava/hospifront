"""Bed allocation request endpoints."""

import builtins

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from hospital_operations.api.common import (
    has_next,
    pagination,
    parse_json,
    require_api_role,
    require_value,
    split_names,
)
from hospital_operations.utils.audit import log_event
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

REQUIREMENT_TYPES = {
    "ICU_LEVEL",
    "VENTILATOR",
    "OXYGEN",
    "ISOLATION",
    "CARDIAC_MONITORING",
    "ACCESSIBILITY",
    "SPECIALTY",
    "OTHER",
}
OPERATORS = {"=", "!=", ">=", "<=", "IN", "NOT IN"}


@frappe.whitelist(methods=["POST"])
def create(
    patient=None,
    inpatient_record=None,
    source="Direct Admission",
    priority="Routine",
    care_level="Other",
    requested_at=None,
    requested_by=None,
    medical_department=None,
    clinical_approval_reference=None,
    idempotency_key=None,
    requirements=None,
    notes=None,
    company=None,
):
    """Create a validated allocation request with optional idempotency."""

    require_api_role(*ALLOWED_ROLES)
    require_value(patient, "patient")

    patient_row = frappe.db.get_value(
        "Patient", patient, ["name", "patient_name"], as_dict=True
    )
    if not patient_row:
        frappe.throw(_("Patient not found."), frappe.ValidationError)

    if inpatient_record:
        record_patient = frappe.db.get_value(
            "Inpatient Record", inpatient_record, "patient"
        )
        if not record_patient:
            frappe.throw(
                _("Inpatient Record not found."), frappe.ValidationError
            )
        if record_patient != patient:
            frappe.throw(
                _("Inpatient Record does not belong to the patient."),
                frappe.ValidationError,
            )

    key = _clean(idempotency_key) or frappe.generate_hash(length=24)
    existing = frappe.db.get_value(
        "Bed Allocation Request",
        {"idempotency_key": key},
        ["name", "patient"],
        as_dict=True,
    )
    if existing:
        if existing.patient != patient:
            frappe.throw(
                _("idempotency_key is already used by another patient."),
                frappe.ValidationError,
            )
        return _serialize(frappe.get_doc("Bed Allocation Request", existing.name), True)

    doc = frappe.get_doc(
        {
            "doctype": "Bed Allocation Request",
            "patient": patient,
            "patient_name": patient_row.patient_name,
            "inpatient_record": inpatient_record or None,
            "source": source or "Direct Admission",
            "priority": priority or "Routine",
            "care_level": care_level or "Other",
            "requested_at": get_datetime(requested_at)
            if requested_at
            else now_datetime(),
            "requested_by": requested_by or frappe.session.user,
            "status": "PENDING",
            "company": company or _default_company(),
            "medical_department": medical_department or None,
            "clinical_approval_reference": clinical_approval_reference or None,
            "idempotency_key": key,
            "clinical_requirements": _requirements(requirements),
            "notes": notes or None,
        }
    ).insert()

    log_event(
        "REQUEST_CREATED",
        patient=doc.patient,
        inpatient_record=doc.inpatient_record,
        allocation_request=doc.name,
        new_state=doc.status,
        reason=_clean(notes) or "Allocation request created",
        details={
            "source": doc.source,
            "priority": doc.priority,
            "care_level": doc.care_level,
            "requirement_count": len(doc.clinical_requirements or []),
        },
    )
    return _serialize(doc, True)


@frappe.whitelist(methods=["GET"])
def list(
    patient=None,
    inpatient_record=None,
    status=None,
    priority=None,
    source=None,
    care_level=None,
    limit=None,
    start=None,
):
    """Return a paginated, sanitized request list."""

    require_api_role(*ALLOWED_ROLES)
    limit, start = pagination(limit, start)

    filters = {}
    if patient:
        filters["patient"] = patient
    if inpatient_record:
        filters["inpatient_record"] = inpatient_record
    if priority:
        filters["priority"] = priority
    if source:
        filters["source"] = source
    if care_level:
        filters["care_level"] = care_level
    statuses = split_names(status)
    if statuses:
        filters["status"] = ("in", statuses)

    total = frappe.db.count("Bed Allocation Request", filters)
    rows = frappe.get_all(
        "Bed Allocation Request",
        filters=filters,
        fields=[
            "name",
            "patient",
            "inpatient_record",
            "source",
            "priority",
            "care_level",
            "requested_at",
            "requested_by",
            "status",
            "company",
        ],
        order_by="requested_at desc, name desc",
        limit_start=start,
        limit_page_length=limit,
    )
    return {
        "data": rows,
        "total": total,
        "limit": limit,
        "start": start,
        "has_next": has_next(start, len(rows), total),
    }


def _requirements(value):
    raw = parse_json(value, "requirements")
    if raw in (None, ""):
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, builtins.list):
        frappe.throw(_("requirements must be a list."), frappe.ValidationError)

    rows = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            frappe.throw(
                _("Requirement row {0} must be an object.").format(index),
                frappe.ValidationError,
            )
        requirement_type = _clean(item.get("requirement_type"))
        if requirement_type not in REQUIREMENT_TYPES:
            frappe.throw(
                _("Invalid requirement_type in row {0}.").format(index),
                frappe.ValidationError,
            )
        operator = _clean(item.get("operator")) or "="
        if operator not in OPERATORS:
            frappe.throw(
                _("Invalid operator in row {0}.").format(index),
                frappe.ValidationError,
            )
        rows.append(
            {
                "requirement_type": requirement_type,
                "operator": operator,
                "required_value": _clean(item.get("required_value")) or "",
                "mandatory": 1 if item.get("mandatory") in (1, "1", True) else 0,
                "clinical_reason": _clean(item.get("clinical_reason")) or None,
            }
        )
    return rows


def _serialize(doc, include_requirements=False):
    result = {
        "name": doc.name,
        "patient": doc.patient,
        "inpatient_record": doc.inpatient_record,
        "source": doc.source,
        "priority": doc.priority,
        "care_level": doc.care_level,
        "requested_at": doc.requested_at,
        "requested_by": doc.requested_by,
        "status": doc.status,
        "company": doc.company,
        "medical_department": doc.medical_department,
        "clinical_approval_reference": doc.clinical_approval_reference,
    }
    if include_requirements:
        result["requirements"] = [
            {
                "requirement_type": row.requirement_type,
                "operator": row.operator,
                "required_value": row.required_value,
                "mandatory": bool(row.mandatory),
                "clinical_reason": row.clinical_reason,
            }
            for row in (doc.clinical_requirements or [])
        ]
    return result


def _default_company():
    return frappe.defaults.get_defaults().get("company") or frappe.db.get_value(
        "Company", {}, "name"
    )


def _clean(value):
    return value.strip() if isinstance(value, str) else value
