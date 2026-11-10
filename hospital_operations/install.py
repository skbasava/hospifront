import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

OPERATIONAL_ROLES = (
    "Bed Manager",
    "Admission Coordinator",
    "Nursing Supervisor",
    "Housekeeping Staff",
    "Hospital Operations Administrator",
)

OPERATIONAL_DOCTYPES = (
    "Bed Allocation Request",
    "Bed Clinical Requirement",
    "Bed Capability",
    "Bed Operational State",
    "Bed Reservation",
    "Bed Staffing Snapshot",
    "Bed Allocation Policy",
    "Bed Allocation Run",
    "Bed Allocation Proposal",
    "Bed Allocation Event",
    "Housekeeping Task",
    "Ward Nurse Assignment",
)

CUSTOM_FIELDS = {
    "Healthcare Service Unit": (
        {
            "fieldname": "ho_ward",
            "label": "HO Ward",
            "fieldtype": "Link",
            "options": "Healthcare Service Unit",
            "insert_after": "parent_healthcare_service_unit",
            "description": "Ward-level service unit this bed belongs to.",
        },
        {
            "fieldname": "ho_is_allocatable",
            "label": "HO Is Allocatable",
            "fieldtype": "Check",
            "default": "0",
            "insert_after": "ho_ward",
            "description": "Only allocatable service units are offered as beds.",
        },
        {
            "fieldname": "ho_care_level",
            "label": "HO Care Level",
            "fieldtype": "Select",
            "options": "\nICU\nGeneral\nSpecial\nOther",
            "insert_after": "ho_is_allocatable",
            "description": "Care level offered by this physical bed.",
        },
    ),
}

INDEXES = (
    ("Bed Allocation Request", "idx_bar_status_priority_requested", ("status", "priority", "requested_at")),
    ("Bed Allocation Request", "idx_bar_patient", ("patient",)),
    ("Bed Capability", "idx_bcap_unit_type", ("healthcare_service_unit", "capability_type")),
    ("Bed Reservation", "idx_bres_unit_status", ("healthcare_service_unit", "status")),
    ("Bed Reservation", "idx_bres_expiry_status", ("expires_at", "status")),
    ("Bed Allocation Proposal", "idx_bap_run", ("allocation_run",)),
    ("Housekeeping Task", "idx_hk_unit_status", ("healthcare_service_unit", "status")),
    ("Bed Staffing Snapshot", "idx_bss_ward_calculated", ("ward", "calculated_at")),
)

ACTIVE_CLAIM_COLUMN = "active_immediate_claim"
ACTIVE_CLAIM_INDEX = "uq_bres_active_immediate_claim"


def before_install():
    # Roles must exist before DocType permissions are synced during install.
    ensure_roles()


def after_install():
    ensure_roles()
    ensure_custom_fields()
    ensure_schema_constraints()


def after_migrate():
    ensure_roles()
    ensure_custom_fields()
    ensure_schema_constraints()


def before_uninstall():
    """Remove additive custom fields so an uninstall is reversible.

    The site must be backed up first; dropping these columns discards bed
    configuration stored on Healthcare Service Unit.
    """
    for doctype, fields in CUSTOM_FIELDS.items():
        for definition in fields:
            name = frappe.db.get_value(
                "Custom Field",
                {"dt": doctype, "fieldname": definition["fieldname"]},
                "name",
            )
            if name:
                frappe.delete_doc(
                    "Custom Field", name, ignore_permissions=True, force=True
                )


def validate_installation():
    """Return a machine-readable installation check for `bench execute`."""
    checks = {}
    for doctype in OPERATIONAL_DOCTYPES:
        checks[f"doctype:{doctype}"] = bool(frappe.db.exists("DocType", doctype))

    for role_name in OPERATIONAL_ROLES:
        checks[f"role:{role_name}"] = bool(frappe.db.exists("Role", role_name))

    for doctype, fields in CUSTOM_FIELDS.items():
        for definition in fields:
            checks[f"custom_field:{doctype}:{definition['fieldname']}"] = bool(
                frappe.db.exists(
                    "Custom Field",
                    {"dt": doctype, "fieldname": definition["fieldname"]},
                )
            )

    for doctype, index_name, _fields in INDEXES:
        checks[f"index:{doctype}:{index_name}"] = _index_exists(
            _table_name(doctype), index_name
        )

    table = _table_name("Bed Reservation")
    checks["column:active_immediate_claim"] = _column_exists(
        table, ACTIVE_CLAIM_COLUMN
    )
    checks["index:active_immediate_claim"] = _index_exists(
        table, ACTIVE_CLAIM_INDEX
    )
    duplicates = []
    if checks.get("doctype:Bed Reservation"):
        duplicates = frappe.db.sql(
            f"""
            SELECT `{ACTIVE_CLAIM_COLUMN}`, COUNT(*) AS total
            FROM `{table}`
            WHERE `{ACTIVE_CLAIM_COLUMN}` IS NOT NULL
            GROUP BY `{ACTIVE_CLAIM_COLUMN}`
            HAVING COUNT(*) > 1
            """
        )
    checks["constraint:no_duplicate_active_claims"] = not duplicates
    checks["required_permission:bed_manager_can_create_request"] = bool(
        frappe.db.exists(
            "DocPerm",
            {
                "parent": "Bed Allocation Request",
                "role": "Bed Manager",
                "permlevel": 0,
                "create": 1,
            },
        )
    )
    checks["ok"] = all(checks.values())
    return checks


def ensure_roles():
    for role_name in OPERATIONAL_ROLES:
        if frappe.db.exists("Role", role_name):
            continue
        frappe.get_doc(
            {
                "doctype": "Role",
                "role_name": role_name,
                "desk_access": 1,
            }
        ).insert(ignore_permissions=True)


def ensure_custom_fields():
    for doctype, fields in CUSTOM_FIELDS.items():
        for definition in fields:
            if frappe.db.exists(
                "Custom Field",
                {"dt": doctype, "fieldname": definition["fieldname"]},
            ):
                continue
            create_custom_field(doctype, dict(definition), ignore_validate=True)


def ensure_schema_constraints():
    for doctype, index_name, fields in INDEXES:
        _add_index(doctype, index_name, fields)
    _ensure_active_claim_constraint()


def _table_name(doctype):
    return f"tab{doctype}"


def _index_exists(table, index_name):
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND index_name = %s
            LIMIT 1
            """,
            (table, index_name),
        )
    )


def _add_index(doctype, index_name, fields):
    table = _table_name(doctype)
    if _index_exists(table, index_name):
        return
    columns = ", ".join(f"`{field}`" for field in fields)
    frappe.db.sql(f"ALTER TABLE `{table}` ADD INDEX `{index_name}` ({columns})")


def _column_exists(table, column):
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (table, column),
        )
    )


def _ensure_active_claim_constraint():
    table = _table_name("Bed Reservation")
    if not _column_exists(table, ACTIVE_CLAIM_COLUMN):
        frappe.db.sql(
            f"""
            ALTER TABLE `{table}`
            ADD COLUMN `{ACTIVE_CLAIM_COLUMN}` varchar(140)
            GENERATED ALWAYS AS (
                CASE
                    WHEN `status` = 'ACTIVE' AND `reservation_type` = 'IMMEDIATE'
                    THEN `healthcare_service_unit`
                    ELSE NULL
                END
            ) STORED
            """
        )
    if not _index_exists(table, ACTIVE_CLAIM_INDEX):
        frappe.db.sql(
            f"ALTER TABLE `{table}` ADD UNIQUE INDEX `{ACTIVE_CLAIM_INDEX}` (`{ACTIVE_CLAIM_COLUMN}`)"
        )
