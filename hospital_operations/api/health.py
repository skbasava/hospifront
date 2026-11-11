import frappe
from frappe.utils import now

from hospital_operations import __version__


@frappe.whitelist(methods=["GET"])
def status():
    """Authenticated readiness probe. Never exposes secrets or patient data."""
    if frappe.session.user == "Guest":
        frappe.throw("Not permitted", frappe.PermissionError)

    required_doctypes = (
        "Bed Allocation Request",
        "Bed Operational State",
        "Bed Reservation",
        "Bed Allocation Policy",
        "Bed Allocation Event",
        "Housekeeping Task",
    )
    return {
        "app": "hospital_operations",
        "version": __version__,
        "site": frappe.local.site,
        "timestamp": now(),
        "doctypes_ready": all(frappe.db.exists("DocType", d) for d in required_doctypes),
        "active_claim_constraint": _has_active_claim_constraint(),
    }


def _has_active_claim_constraint():
    if not frappe.db.table_exists("Bed Reservation"):
        return False
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = 'tabBed Reservation'
              AND index_name = 'uq_bres_active_immediate_claim'
            LIMIT 1
            """
        )
    )
