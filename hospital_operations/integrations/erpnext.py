"""Read-only ERPNext and Marley billing integration points.

Billing is deliberately separate from allocation. These helpers expose tariffs,
chargeable occupancy periods, invoices and discharge reconciliation without
creating or modifying invoices, payments or occupancy rows.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

INVOICE_LIMITATION = (
    "There is no direct admission link; Sales Invoices link by patient. "
    "Treat invoice lists as reference data, not admission-level reconciliation."
)


class BillingIntegrationError(ValueError):
    """A billing reference cannot be read."""


def get_service_unit_tariff(service_unit):
    unit = frappe.db.get_value(
        "Healthcare Service Unit",
        service_unit,
        ["service_unit_type", "company"],
        as_dict=True,
    )
    if not unit:
        return {
            "service_unit": service_unit,
            "service_unit_type": None,
            "is_billable": False,
            "inpatient_occupancy": False,
            "item": None,
            "item_code": None,
            "rate": 0.0,
            "currency": None,
            "error": "service_unit_not_found",
        }

    unit_type = frappe.db.get_value(
        "Healthcare Service Unit Type",
        unit.service_unit_type,
        [
            "service_unit_type",
            "is_billable",
            "inpatient_occupancy",
            "item",
            "item_code",
            "rate",
        ],
        as_dict=True,
    )
    currency = None
    if unit.company:
        currency = frappe.db.get_value("Company", unit.company, "default_currency")

    if not unit_type:
        return {
            "service_unit": service_unit,
            "service_unit_type": unit.service_unit_type,
            "is_billable": False,
            "inpatient_occupancy": False,
            "item": None,
            "item_code": None,
            "rate": 0.0,
            "currency": currency,
            "error": "service_unit_type_not_found",
        }

    return {
        "service_unit": service_unit,
        "service_unit_type": unit_type.service_unit_type,
        "is_billable": bool(unit_type.is_billable),
        "inpatient_occupancy": bool(unit_type.inpatient_occupancy),
        "item": unit_type.item,
        "item_code": unit_type.item_code,
        "rate": float(unit_type.rate or 0),
        "currency": currency,
    }


def get_occupancy_billing(inpatient_record):
    record = _get_record(inpatient_record)
    rows = []
    for occupancy in record.get("inpatient_occupancies") or []:
        check_in = get_datetime(occupancy.check_in) if occupancy.check_in else None
        check_out = get_datetime(occupancy.check_out) if occupancy.check_out else None
        end_at = check_out or now_datetime()
        hours = 0.0
        if check_in:
            hours = max((end_at - check_in).total_seconds() / 3600, 0.0)
        tariff = get_service_unit_tariff(occupancy.service_unit)
        rows.append(
            {
                "service_unit": occupancy.service_unit,
                "check_in": occupancy.check_in,
                "check_out": occupancy.check_out,
                "invoiced": bool(occupancy.invoiced),
                "left": bool(occupancy.left),
                "tariff": tariff,
                "billable": bool(tariff["is_billable"] and not occupancy.invoiced),
                "chargeable_hours": round(hours, 2),
                "chargeable_days": round(hours / 24, 2),
            }
        )
    return {
        "inpatient_record": record.name,
        "patient": record.patient,
        "rows": rows,
        "has_billable_occupancy": any(row["billable"] for row in rows),
    }


def get_admission_invoices(inpatient_record):
    record = _get_record(inpatient_record)
    meta = frappe.get_meta("Sales Invoice")
    limitation = INVOICE_LIMITATION
    if not meta.has_field("patient"):
        return {
            "inpatient_record": record.name,
            "patient": record.patient,
            "invoices": [],
            "linked_by": None,
            "limitation": limitation,
            "error": "sales_invoice_patient_field_missing",
        }

    fields = ["name", "docstatus", "posting_date", "grand_total", "outstanding_amount"]
    for optional in ("status", "currency", "service_unit"):
        if meta.has_field(optional):
            fields.append(optional)

    filters = {"patient": record.patient, "docstatus": ("<", 2)}
    rows = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=fields,
        order_by="posting_date desc, name desc",
        limit_page_length=0,
    )
    return {
        "inpatient_record": record.name,
        "patient": record.patient,
        "invoices": rows,
        "linked_by": "patient",
        "limitation": limitation,
    }


def discharge_reconciliation(inpatient_record):
    record = _get_record(inpatient_record)
    pending = {}
    error = None
    try:
        from healthcare.healthcare.doctype.inpatient_record.inpatient_record import (
            get_pending_invoices,
        )

        pending = get_pending_invoices(record) or {}
    except Exception as exc:
        error = str(exc)

    allow_unbilled = bool(
        frappe.db.get_single_value(
            "Healthcare Settings", "allow_discharge_despite_unbilled_services"
        )
    )
    occupancy = get_occupancy_billing(inpatient_record)
    invoices = get_admission_invoices(inpatient_record)
    blocking = bool(pending) and not allow_unbilled
    return {
        "inpatient_record": record.name,
        "patient": record.patient,
        "pending": pending,
        "allow_discharge_despite_unbilled": allow_unbilled,
        "is_clear": not pending,
        "blocking": blocking,
        "occupancy": occupancy,
        "invoices": invoices,
        "has_billing_references": bool(invoices["invoices"]),
        "error": error,
        "notes": (
            "Read-only reconciliation. No invoice is created and no allocation "
            "or occupancy record is changed by this call."
        ),
    }


def _get_record(inpatient_record):
    if not inpatient_record or not frappe.db.exists(
        "Inpatient Record", inpatient_record
    ):
        raise BillingIntegrationError(
            _("Inpatient Record {0} does not exist.").format(inpatient_record)
        )
    return frappe.get_doc("Inpatient Record", inpatient_record)
