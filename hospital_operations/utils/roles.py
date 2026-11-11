import frappe

BED_MANAGER = "Bed Manager"
ADMISSION_COORDINATOR = "Admission Coordinator"
NURSING_SUPERVISOR = "Nursing Supervisor"
HOUSEKEEPING_STAFF = "Housekeeping Staff"
OPERATIONS_ADMIN = "Hospital Operations Administrator"

OPERATIONAL_ROLES = (
    BED_MANAGER,
    ADMISSION_COORDINATOR,
    NURSING_SUPERVISOR,
    HOUSEKEEPING_STAFF,
    OPERATIONS_ADMIN,
)

ADMIN_ROLES = (OPERATIONS_ADMIN, "System Manager", "Healthcare Administrator")


def user_roles(user=None):
    return set(frappe.get_roles(user or frappe.session.user))


def has_any_role(*roles, user=None):
    return bool(user_roles(user).intersection(roles))


def require_any_role(*roles):
    if not has_any_role(*roles):
        frappe.throw(
            "You do not have permission to perform this action.",
            frappe.PermissionError,
        )


def require_operational_role():
    require_any_role(*OPERATIONAL_ROLES, *ADMIN_ROLES)


def has_app_permission():
    return has_any_role(*OPERATIONAL_ROLES, *ADMIN_ROLES)
