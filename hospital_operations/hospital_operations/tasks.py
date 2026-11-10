import frappe
from frappe.utils import add_to_date, now

RESERVATION_GRACE_MINUTES = 5
STAFFING_SNAPSHOT_RETENTION_DAYS = 30


def expire_stale_reservations():
    cutoff = add_to_date(now(), minutes=-RESERVATION_GRACE_MINUTES)
    expired = frappe.get_all(
        "Bed Reservation",
        filters={"status": "ACTIVE", "expires_at": ("<", cutoff)},
        pluck="name",
    )
    for name in expired:
        try:
            doc = frappe.get_doc("Bed Reservation", name)
            doc.status = "EXPIRED"
            doc.save(ignore_permissions=True)
        except frappe.DoesNotExistError:
            continue
    return {"expired": len(expired)}


def reconcile_bed_reservations():
    orphaned = frappe.db.sql(
        """
        SELECT br.name
        FROM `tabBed Reservation` br
        LEFT JOIN `tabBed Allocation Request` bar ON bar.name = br.allocation_request
        WHERE br.status IN ('ACTIVE', 'CONFIRMED')
          AND (bar.name IS NULL OR bar.status = 'CANCELLED')
        """,
        as_dict=True,
    )
    for row in orphaned:
        frappe.db.set_value(
            "Bed Reservation",
            row.name,
            "status",
            "CANCELLED",
            update_modified=True,
        )
    return {"reconciled": len(orphaned)}


def prune_expired_staffing_snapshots():
    cutoff = add_to_date(now(), days=-STAFFING_SNAPSHOT_RETENTION_DAYS)
    filters = {"calculated_at": ("<", cutoff)}
    removed = frappe.db.count("Bed Staffing Snapshot", filters)
    frappe.db.delete("Bed Staffing Snapshot", filters)
    return {"removed": removed}
