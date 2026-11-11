import threading
import unittest

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
    from frappe.utils import now_datetime
    from pymysql.err import IntegrityError
except ImportError:
    raise unittest.SkipTest("Frappe bench required to run concurrency tests")

from hospital_operations.bed_allocation import reservation
from hospital_operations.bed_allocation.transaction import ReservationConflict
from hospital_operations.tests import factories


class TestReservationConcurrency(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = factories.get_company()
        cls.unit_type = factories.make_service_unit_type()
        cls.ward = factories.make_ward(cls.company)
        cls.bed = factories.make_bed(cls.unit_type, "ICU", cls.ward, cls.company)
        cls.policy = factories.make_policy()

        cls.patient_a = factories.make_patient()
        cls.inpatient_a = factories.make_inpatient_record(
            cls.patient_a, cls.unit_type, cls.company
        )
        cls.request_a = factories.make_request(
            cls.patient_a, cls.inpatient_a, company=cls.company
        )
        cls.allocation_run, cls.proposal_a = factories.make_allocation(
            cls.bed, cls.request_a, cls.policy
        )

        cls.patient_b = factories.make_patient()
        cls.inpatient_b = factories.make_inpatient_record(
            cls.patient_b, cls.unit_type, cls.company
        )
        cls.request_b = factories.make_request(
            cls.patient_b, cls.inpatient_b, company=cls.company
        )
        cls.request_b.status = "PROPOSED"
        cls.request_b.save(ignore_permissions=True)
        cls.proposal_b = factories.make_proposal_for_bed(
            cls.bed, cls.request_b, cls.allocation_run
        )

        cls.site = frappe.local.site
        cls.sites_path = frappe.local.sites_path
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()
        super().tearDownClass()

    @classmethod
    def _cleanup(cls):
        requests = [cls.request_a.name, cls.request_b.name]
        beds = [cls.bed.name, cls.ward.name]
        ips = [cls.inpatient_a.name, cls.inpatient_b.name]
        try:
            frappe.db.delete(
                "Bed Allocation Event", {"allocation_request": ("in", requests)}
            )
            frappe.db.delete("Bed Allocation Event", {"bed": ("in", beds)})
            frappe.db.delete("Bed Reservation", {"allocation_request": ("in", requests)})
            frappe.db.delete(
                "Bed Allocation Proposal", {"allocation_request": ("in", requests)}
            )
            frappe.db.delete("Bed Allocation Run", {"name": cls.allocation_run.name})
            frappe.db.delete("Bed Operational State", {"healthcare_service_unit": ("in", beds)})
            frappe.db.delete("Inpatient Occupancy", {"parent": ("in", ips)})
            frappe.db.delete("Bed Allocation Request", {"name": ("in", requests)})
            for name in ips:
                if frappe.db.exists("Inpatient Record", name):
                    frappe.delete_doc(
                        "Inpatient Record", name, force=True, ignore_permissions=True
                    )
            for patient in (cls.patient_a.name, cls.patient_b.name):
                if frappe.db.exists("Patient", patient):
                    frappe.delete_doc("Patient", patient, force=True, ignore_permissions=True)
            for bed in (cls.bed.name, cls.ward.name):
                if frappe.db.exists("Healthcare Service Unit", bed):
                    frappe.delete_doc(
                        "Healthcare Service Unit", bed, force=True, ignore_permissions=True
                    )
            if frappe.db.exists(
                "Healthcare Service Unit Type", cls.unit_type.name
            ):
                frappe.delete_doc(
                    "Healthcare Service Unit Type",
                    cls.unit_type.name,
                    force=True,
                    ignore_permissions=True,
                )
            if frappe.db.exists("Bed Allocation Policy", cls.policy.name):
                frappe.delete_doc(
                    "Bed Allocation Policy",
                    cls.policy.name,
                    force=True,
                    ignore_permissions=True,
                )
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()

    @classmethod
    def _confirm_in_thread(cls, proposal, key, barrier, results):
        frappe.init(site=cls.site, sites_path=cls.sites_path)
        frappe.connect()
        try:
            frappe.set_user("Administrator")
            barrier.wait(timeout=15)
            try:
                results.append(("success", reservation.confirm(proposal, idempotency_key=key)))
                frappe.db.commit()
            except Exception as exc:
                frappe.db.rollback()
                results.append(("error", exc))
        finally:
            frappe.destroy()

    def test_two_simultaneous_confirms_only_one_wins(self):
        barrier = threading.Barrier(2)
        results = []
        threads = [
            threading.Thread(
                target=self._confirm_in_thread,
                args=(self.proposal_a.name, "concurrent-a", barrier, results),
            ),
            threading.Thread(
                target=self._confirm_in_thread,
                args=(self.proposal_b.name, "concurrent-b", barrier, results),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        successes = [item for item in results if item[0] == "success"]
        failures = [item for item in results if item[0] == "error"]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0][1], ReservationConflict)

        self.assertEqual(
            frappe.db.get_value(
                "Bed Operational State",
                {"healthcare_service_unit": self.bed.name},
                "state",
            ),
            "OCCUPIED",
        )
        self.assertEqual(
            frappe.db.count(
                "Bed Reservation",
                {"healthcare_service_unit": self.bed.name, "status": "CONFIRMED"},
            ),
            1,
        )
        occupied = frappe.get_all(
            "Inpatient Occupancy",
            filters={
                "service_unit": self.bed.name,
                "parenttype": "Inpatient Record",
                "check_out": ("is", "not set"),
            },
            pluck="parent",
        )
        self.assertEqual(len(occupied), 1)

    def test_database_rejects_two_active_immediate_claims(self):
        now = now_datetime()
        insert = """
            INSERT INTO `tabBed Reservation`
                (name, creation, modified, modified_by, owner, docstatus, idx,
                 allocation_request, healthcare_service_unit, reservation_type,
                 status, start_at, expires_at, created_by, idempotency_key)
            VALUES (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
                    %s, %s, 'IMMEDIATE', 'ACTIVE', %s, %s, 'Administrator', %s)
        """
        try:
            frappe.db.sql(
                insert,
                (
                    f"BRES-RAW-{frappe.generate_hash(length=6)}",
                    now,
                    now,
                    self.request_a.name,
                    self.bed.name,
                    now,
                    now,
                    f"raw-{frappe.generate_hash(length=6)}",
                ),
            )
            with self.assertRaises(IntegrityError):
                frappe.db.sql(
                    insert,
                    (
                        f"BRES-RAW-{frappe.generate_hash(length=6)}",
                        now,
                        now,
                        self.request_a.name,
                        self.bed.name,
                        now,
                        now,
                        f"raw-{frappe.generate_hash(length=6)}",
                    ),
                )
        finally:
            frappe.db.rollback()
