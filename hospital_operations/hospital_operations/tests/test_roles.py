import unittest

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
except ImportError:
    raise unittest.SkipTest("Frappe bench required for role integration tests")

from hospital_operations.install import OPERATIONAL_ROLES


class TestOperationalRoles(FrappeTestCase):
    def test_roles_exist(self):
        for role in OPERATIONAL_ROLES:
            self.assertTrue(frappe.db.exists("Role", role), msg=f"Missing role: {role}")

    def test_bed_manager_cannot_delete_requests(self):
        perm = frappe.get_all(
            "DocPerm",
            filters={
                "parent": "Bed Allocation Request",
                "role": "Bed Manager",
                "permlevel": 0,
            },
            fields=["read", "write", "create", "delete"],
        )
        self.assertTrue(perm)
        self.assertFalse(perm[0].get("delete"))
