import frappe

from hospital_operations.install import ensure_schema_constraints as _ensure_schema_constraints


def ensure_schema_constraints():
    if not frappe.db.exists("DocType", "Bed Reservation"):
        return
    _ensure_schema_constraints()
