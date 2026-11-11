import unittest

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
except ImportError:
    raise unittest.SkipTest("Frappe bench required to run billing integration tests")

from hospital_operations.integrations import erpnext, marley
from hospital_operations.tests import factories


class TestBillingIntegration(FrappeTestCase):
    def setUp(self):
        self.company = factories.get_company()
        self.unit_type = factories.make_service_unit_type()
        self.ward = factories.make_ward(self.company)
        self.bed = factories.make_bed(self.unit_type, "ICU", self.ward, self.company)
        self.patient = factories.make_patient()
        self.inpatient = factories.make_inpatient_record(
            self.patient, self.unit_type, self.company
        )

    def test_non_billable_tariff_is_read_without_creating_items(self):
        item_count = frappe.db.count("Item")

        tariff = erpnext.get_service_unit_tariff(self.bed.name)

        self.assertEqual(tariff["service_unit"], self.bed.name)
        self.assertEqual(tariff["service_unit_type"], self.unit_type.name)
        self.assertFalse(tariff["is_billable"])
        self.assertTrue(tariff["inpatient_occupancy"])
        self.assertIsNone(tariff["item"])
        self.assertEqual(frappe.db.count("Item"), item_count)

    def test_billable_tariff_reads_item_and_rate(self):
        billable_type = factories.make_billable_service_unit_type(rate=250)
        bed = factories.make_bed(billable_type, "ICU", self.ward, self.company)

        tariff = erpnext.get_service_unit_tariff(bed.name)

        self.assertTrue(tariff["is_billable"])
        self.assertTrue(tariff["inpatient_occupancy"])
        self.assertEqual(tariff["item"], billable_type.item)
        self.assertEqual(float(tariff["rate"]), 250.0)

    def test_occupancy_billing_reports_active_period_without_invoice(self):
        marley.admit(self.inpatient.name, self.bed.name)
        invoice_count = frappe.db.count("Sales Invoice")

        result = erpnext.get_occupancy_billing(self.inpatient.name)

        self.assertEqual(result["inpatient_record"], self.inpatient.name)
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["service_unit"], self.bed.name)
        self.assertIsNone(row["check_out"])
        self.assertFalse(row["invoiced"])
        self.assertFalse(row["billable"])
        self.assertGreaterEqual(row["chargeable_hours"], 0)
        self.assertEqual(frappe.db.count("Sales Invoice"), invoice_count)

    def test_discharge_reconciliation_clear_for_non_billable_occupancy(self):
        marley.admit(self.inpatient.name, self.bed.name)

        result = erpnext.discharge_reconciliation(self.inpatient.name)

        self.assertTrue(result["is_clear"])
        self.assertFalse(result["blocking"])
        self.assertEqual(result["pending"], {})
        self.assertEqual(len(result["occupancy"]["rows"]), 1)
        self.assertFalse(result["has_billing_references"])

    def test_discharge_reconciliation_detects_unbilled_billable_occupancy(self):
        billable_type = factories.make_billable_service_unit_type(rate=250)
        bed = factories.make_bed(billable_type, "ICU", self.ward, self.company)
        patient = factories.make_patient()
        inpatient = factories.make_inpatient_record(
            patient, billable_type, self.company
        )
        marley.admit(inpatient.name, bed.name)
        invoice_count = frappe.db.count("Sales Invoice")

        result = erpnext.discharge_reconciliation(inpatient.name)

        self.assertFalse(result["is_clear"])
        self.assertTrue(result["blocking"])
        self.assertIn("Inpatient Occupancy", result["pending"])
        self.assertEqual(result["occupancy"]["rows"][0]["billable"], True)
        self.assertEqual(frappe.db.count("Sales Invoice"), invoice_count)

    def test_admission_invoices_are_read_only_and_linked_by_patient(self):
        invoices = erpnext.get_admission_invoices(self.inpatient.name)

        self.assertEqual(invoices["inpatient_record"], self.inpatient.name)
        self.assertEqual(invoices["patient"], self.patient.name)
        self.assertEqual(invoices["invoices"], [])
        self.assertEqual(invoices["linked_by"], "patient")
        self.assertIn("no direct admission link", invoices["limitation"])
