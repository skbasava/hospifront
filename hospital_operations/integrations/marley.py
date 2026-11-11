"""The only write boundary between hospital_operations and Marley ADT.

All admission, transfer and discharge work goes through Marley's supported
methods. No upstream controller is modified. Callers must hold the relevant bed
and Inpatient Record row locks and wrap this module in a savepoint.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime
from healthcare.healthcare.doctype.inpatient_record import (
    inpatient_record as marley,
)

from hospital_operations.bed_allocation.transaction import ReservationError

ADMISSION_STATUS = "Admission Scheduled"
ADMITTED_STATUS = "Admitted"
DISCHARGE_SCHEDULED_STATUS = "Discharge Scheduled"
DISCHARGED_STATUS = "Discharged"


class MarleyError(ReservationError):
    """Marley rejected or failed an ADT operation."""


def get_active_occupancies(inpatient_record):
    return frappe.db.sql(
        """
        SELECT name, service_unit, check_in
        FROM `tabInpatient Occupancy`
        WHERE parent = %s
          AND parenttype = 'Inpatient Record'
          AND parentfield = 'inpatient_occupancies'
          AND check_out IS NULL
        ORDER BY idx
        """,
        (inpatient_record,),
        as_dict=True,
    )


def get_active_occupancies_for_update(inpatient_record):
    """Current read of active occupancy rows, used before taking bed locks."""

    return frappe.db.sql(
        """
        SELECT name, service_unit, check_in
        FROM `tabInpatient Occupancy`
        WHERE parent = %s
          AND parenttype = 'Inpatient Record'
          AND parentfield = 'inpatient_occupancies'
          AND check_out IS NULL
        ORDER BY idx
        FOR UPDATE
        """,
        (inpatient_record,),
        as_dict=True,
    )


def get_active_bed(inpatient_record):
    rows = get_active_occupancies(inpatient_record)
    if not rows:
        return None
    if len(rows) > 1:
        raise MarleyError(
            _("Inpatient Record {0} has more than one active bed.").format(
                inpatient_record
            )
        )
    return rows[0].service_unit


def _get_record(inpatient_record):
    if not inpatient_record or not frappe.db.exists(
        "Inpatient Record", inpatient_record
    ):
        raise MarleyError(
            _("Inpatient Record {0} does not exist.").format(inpatient_record)
        )
    return frappe.get_doc("Inpatient Record", inpatient_record)


def assert_admission_ready(inpatient_record):
    doc = _get_record(inpatient_record)
    if doc.status != ADMISSION_STATUS:
        raise MarleyError(
            _("Inpatient Record {0} is {1}; admission requires {2}.").format(
                doc.name, doc.status, ADMISSION_STATUS
            )
        )
    if get_active_occupancies(doc.name):
        raise MarleyError(
            _("Inpatient Record {0} already has an active bed.").format(doc.name)
        )
    return doc


def admit(inpatient_record, bed, check_in=None, expected_discharge=None):
    """Admit a scheduled inpatient into ``bed`` using Marley's own workflow."""

    doc = assert_admission_ready(inpatient_record)
    check_in = check_in or now_datetime()
    try:
        doc.admit(bed, check_in, expected_discharge)
    except Exception as exc:
        raise MarleyError(
            _("Marley admission failed for {0}: {1}").format(inpatient_record, exc)
        ) from exc

    active = get_active_bed(inpatient_record)
    if active != bed:
        raise MarleyError(
            _("Marley did not place {0} in {1}.").format(inpatient_record, bed)
        )
    return doc


def assert_transfer_ready(inpatient_record, from_bed=None):
    doc = _get_record(inpatient_record)
    if doc.status != ADMITTED_STATUS:
        raise MarleyError(
            _("Inpatient Record {0} is {1}; transfer requires {2}.").format(
                doc.name, doc.status, ADMITTED_STATUS
            )
        )
    active = get_active_bed(doc.name)
    if not active:
        raise MarleyError(
            _("Inpatient Record {0} has no active bed to transfer from.").format(
                doc.name
            )
        )
    if from_bed and active != from_bed:
        raise MarleyError(
            _("Inpatient Record {0} is in {1}, not {2}.").format(
                doc.name, active, from_bed
            )
        )
    return doc, active


def transfer(inpatient_record, to_bed, from_bed=None, check_in=None):
    """Move an admitted patient to ``to_bed``; the old bed becomes vacant."""

    doc, active = assert_transfer_ready(inpatient_record, from_bed)
    check_in = check_in or now_datetime()
    try:
        doc.transfer(to_bed, check_in, active)
    except Exception as exc:
        raise MarleyError(
            _("Marley transfer failed for {0}: {1}").format(inpatient_record, exc)
        ) from exc

    new_active = get_active_bed(inpatient_record)
    if new_active != to_bed:
        raise MarleyError(
            _("Marley did not move {0} to {1}.").format(inpatient_record, to_bed)
        )
    return doc, active


def discharge(inpatient_record):
    """Discharge an inpatient and release every active occupancy.

    Marley validates nursing tasks, unbilled services and incomplete requests.
    A failure raised here must be rolled back by the caller's savepoint.
    """

    doc = _get_record(inpatient_record)
    if doc.status == DISCHARGED_STATUS and not get_active_occupancies(doc.name):
        return doc, []

    if doc.status not in (ADMITTED_STATUS, DISCHARGE_SCHEDULED_STATUS):
        raise MarleyError(
            _("Inpatient Record {0} is {1}; discharge requires {2} or {3}.").format(
                doc.name,
                doc.status,
                ADMITTED_STATUS,
                DISCHARGE_SCHEDULED_STATUS,
            )
        )

    released = [row.service_unit for row in get_active_occupancies(doc.name)]
    if not released:
        raise MarleyError(
            _("Inpatient Record {0} has no active bed to release.").format(doc.name)
        )

    try:
        marley.check_out_inpatient(doc)
        marley.discharge_patient(doc)
    except Exception as exc:
        raise MarleyError(
            _("Marley discharge failed for {0}: {1}").format(inpatient_record, exc)
        ) from exc

    if doc.status != DISCHARGED_STATUS:
        raise MarleyError(
            _("Marley did not mark {0} as discharged.").format(inpatient_record)
        )
    if get_active_occupancies(inpatient_record):
        raise MarleyError(
            _("Inpatient Record {0} still has an active occupancy.").format(
                inpatient_record
            )
        )
    return doc, released
