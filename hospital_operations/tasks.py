import frappe
from frappe.utils import add_to_date, now

from hospital_operations.bed_allocation.reservation import expire, reconcile

STAFFING_SNAPSHOT_RETENTION_DAYS = 30


def expire_stale_reservations():
    """Expire stale ACTIVE claims and release their beds to READY."""

    return expire(now=now())


def reconcile_bed_reservations():
    """Repair orphan reservations and RESERVED beds without an active claim."""

    return reconcile()


def refresh_staffing_snapshots():
    """Persist live HRMS-derived staffing capacity for every ward."""

    from hospital_operations.bed_allocation import staffing_service

    return staffing_service.refresh_all_wards()


def reconcile_housekeeping_tasks():
    """Create cleaning tasks for cleaning beds that have none."""

    from hospital_operations.bed_allocation import housekeeping

    return housekeeping.reconcile_cleaning_tasks()


def prune_expired_staffing_snapshots():
    cutoff = add_to_date(now(), days=-STAFFING_SNAPSHOT_RETENTION_DAYS)
    filters = {"calculated_at": ("<", cutoff)}
    removed = frappe.db.count("Bed Staffing Snapshot", filters)
    frappe.db.delete("Bed Staffing Snapshot", filters)
    return {"removed": removed}
