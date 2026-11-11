app_name = "hospital_operations"
app_title = "Hospital Operations"
app_publisher = "Hospital Operations"
app_description = (
    "Operational Bed Allocation System for Frappe Health, ERPNext and HRMS"
)
app_email = "operations@example.invalid"
app_license = "MIT"

required_apps = ["frappe", "erpnext", "hrms", "healthcare"]

add_to_apps_screen = [
    {
        "name": "hospital_operations",
        "logo": "/assets/hospital_operations/images/hospital-operations.svg",
        "title": "Hospital Operations",
        "route": "/app",
        "has_permission": "hospital_operations.utils.roles.has_app_permission",
    }
]

before_install = "hospital_operations.install.before_install"
after_install = "hospital_operations.install.after_install"
after_migrate = "hospital_operations.install.after_migrate"
before_uninstall = "hospital_operations.install.before_uninstall"

scheduler_events = {
    "hourly": [
        "hospital_operations.tasks.expire_stale_reservations",
        "hospital_operations.tasks.refresh_staffing_snapshots",
    ],
    "daily": [
        "hospital_operations.tasks.reconcile_bed_reservations",
        "hospital_operations.tasks.reconcile_housekeeping_tasks",
        "hospital_operations.tasks.prune_expired_staffing_snapshots",
    ],
}

fixtures = []
