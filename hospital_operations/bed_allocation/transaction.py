"""Transaction and locking helpers for bed claims.

The reservation service uses MariaDB row locks, not only application checks.
Every flow that can change a physical bed locks the same rows in a stable order
so two concurrent confirmations cannot both succeed.
"""

import re
from contextlib import contextmanager

import frappe
from frappe import _


class ReservationError(frappe.ValidationError):
    """A reservation operation cannot be completed safely."""

    http_status_code = 400


class ReservationConflict(ReservationError):
    """The requested bed changed while the caller was deciding."""

    http_status_code = 409


class StaleProposal(ReservationConflict):
    """The proposal no longer matches the bed's operational state."""


def lock_documents(doctype, names):
    """Lock document rows with ``SELECT ... FOR UPDATE``.

    Names are de-duplicated and sorted to keep lock acquisition deterministic.
    Missing rows are treated as a conflict because a caller must not act on a
    document that disappeared after it was read.
    """

    if isinstance(names, str):
        names = [names]
    unique = sorted({name for name in names if name})
    if not unique:
        return []

    table = f"`tab{doctype}`"
    placeholders = ", ".join(["%s"] * len(unique))
    rows = frappe.db.sql(
        f"SELECT name FROM {table} WHERE name IN ({placeholders}) ORDER BY name FOR UPDATE",
        tuple(unique),
        as_dict=True,
    )
    found = {row.name for row in rows}
    missing = [name for name in unique if name not in found]
    if missing:
        raise ReservationConflict(
            _("Record disappeared while locking: {0}").format(", ".join(missing))
        )
    return unique


def lock_document_values(doctype, name, fields):
    """Lock one row and return current values without starting a snapshot.

    ``SELECT ... FOR UPDATE`` is a current read. Calling this before any ordinary
    ORM read prevents a REPEATABLE READ transaction from acting on stale values
    after it waited for another allocation.
    """

    if not name:
        raise ReservationConflict(_("A document name is required for locking."))
    columns = ", ".join(f"`{field}`" for field in fields)
    rows = frappe.db.sql(
        f"SELECT {columns} FROM `tab{doctype}` WHERE name = %s FOR UPDATE",
        (name,),
        as_dict=True,
    )
    if not rows:
        raise ReservationConflict(
            _("{0} {1} disappeared while locking.").format(doctype, name)
        )
    return rows[0]


def lock_beds(bed_names):
    return lock_documents("Healthcare Service Unit", bed_names)


def lock_bed_states(bed_names):
    """Lock current operational-state rows for the given beds.

    A missing state row is tolerated: the caller holds the HSU lock and will
    create the state inside the same transaction.
    """

    if isinstance(bed_names, str):
        bed_names = [bed_names]
    unique = sorted({name for name in bed_names if name})
    if not unique:
        return []
    placeholders = ", ".join(["%s"] * len(unique))
    return frappe.db.sql(
        f"""
        SELECT name, healthcare_service_unit
        FROM `tabBed Operational State`
        WHERE healthcare_service_unit IN ({placeholders})
        ORDER BY healthcare_service_unit
        FOR UPDATE
        """,
        tuple(unique),
        as_dict=True,
    )


def lock_inpatient_record(inpatient_record):
    if not inpatient_record:
        return []
    return lock_documents("Inpatient Record", [inpatient_record])


def _savepoint_name(name):
    safe = re.sub(r"[^0-9A-Za-z_]", "_", str(name))
    return ("sp_" + safe)[:60]


@contextmanager
def savepoint(name="ho_reservation"):
    """Run a nested transaction and roll it back on any exception.

    The caller's request transaction remains active. This lets a failed Marley
    admission undo the reservation and bed-state writes without committing the
    surrounding request. We never call ``frappe.db.commit`` here.
    """

    name = _savepoint_name(name)
    frappe.db.savepoint(name)
    try:
        yield
    except Exception:
        frappe.db.rollback(save_point=name)
        raise
    else:
        try:
            frappe.db.release_savepoint(name)
        except Exception:
            # Release is best-effort; the savepoint remains harmless.
            pass
