app_name = "hospital_operations"
app_title = "Hospital Operations"
app_publisher = "Hospital Operations"
app_description = (
    "Operational Bed Allocation System for Frappe Health, ERPNext and HRMS"
)
app_email = "operations@example.invalid"
app_license = "MIT"

required_apps = ["frappe", "erpnext", "hrms", "healthcare"]

before_install = "hospital_operations.install.before_install"
after_install = "hospital_operations.install.after_install"
after_migrate = "hospital_operations.install.after_migrate"
before_uninstall = "hospital_operations.install.before_uninstall"

scheduler_events = {
    "hourly": [
        "hospital_operations.tasks.expire_stale_reservations",
    ],
    "daily": [
        "hospital_operations.tasks.reconcile_bed_reservations",
        "hospital_operations.tasks.prune_expired_staffing_snapshots",
    ],
}

fixtures = []
