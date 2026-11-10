import unittest

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
except ImportError:
    raise unittest.SkipTest("Frappe bench required for DocType integration tests")

EXPECTED_DOCTYPES = (
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


class TestDocTypeInstallation(FrappeTestCase):
    def test_doctypes_exist(self):
        for doctype in EXPECTED_DOCTYPES:
            self.assertTrue(
                frappe.db.exists("DocType", doctype),
                msg=f"Missing DocType: {doctype}",
            )

    def test_child_table_is_table(self):
        meta = frappe.get_meta("Bed Clinical Requirement")
        self.assertTrue(meta.istable)

    def test_bed_operational_state_unique_per_unit(self):
        meta = frappe.get_meta("Bed Operational State")
        field = meta.get_field("healthcare_service_unit")
        self.assertTrue(field.unique)

    def test_event_is_append_only(self):
        event = frappe.get_doc(
            {
                "doctype": "Bed Allocation Event",
                "event_type": "STATE_CHANGED",
                "reason": "test",
            }
        ).insert(ignore_permissions=True)
        event.reason = "changed"
        with self.assertRaises(frappe.ValidationError):
            event.save(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc(
                "Bed Allocation Event", event.name, ignore_permissions=True
            )
