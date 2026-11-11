import unittest

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
except ImportError:
    raise unittest.SkipTest("Frappe bench required to run housekeeping tests")

from hospital_operations.bed_allocation import housekeeping, operational, reservation
from hospital_operations.tests import factories


class TestHousekeeping(FrappeTestCase):
    def setUp(self):
        self.company = factories.get_company()
        self.unit_type = factories.make_service_unit_type()
        self.ward = factories.make_ward(self.company)
        self.bed = factories.make_bed(self.unit_type, "ICU", self.ward, self.company)
        self.policy = factories.make_policy()
        self.patient = factories.make_patient()
        self.inpatient = factories.make_inpatient_record(
            self.patient, self.unit_type, self.company
        )
        self.request = factories.make_request(
            self.patient, self.inpatient, company=self.company
        )
        self.allocation_run, self.proposal = factories.make_allocation(
            self.bed, self.request, self.policy
        )

    def _state(self, bed=None):
        return operational.get_state_value((bed or self.bed).name)

    def _latest_task(self, bed=None):
        names = frappe.get_all(
            "Housekeeping Task",
            filters={"healthcare_service_unit": (bed or self.bed).name},
            pluck="name",
            order_by="creation desc",
            limit_page_length=1,
        )
        return frappe.get_doc("Housekeeping Task", names[0]) if names else None

    def _cleaning_bed(self):
        operational.set_state(self.bed.name, "OCCUPIED", reason="test occupancy")
        operational.mark_vacated(self.bed.name, reason="test vacate")
        operational.require_cleaning(self.bed.name, reason="test cleaning")

    def test_discharge_creates_pending_cleaning_task(self):
        reservation.confirm(self.proposal.name)

        reservation.discharge(self.inpatient.name)

        task = self._latest_task()
        self.assertIsNotNone(task)
        self.assertEqual(task.task_type, "DISCHARGE_CLEAN")
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(self._state(), "CLEANING_REQUIRED")

    def test_transfer_creates_pending_cleaning_task(self):
        reservation.confirm(self.proposal.name)
        to_bed = factories.make_bed(
            self.unit_type, "ICU", self.ward, self.company
        )

        reservation.transfer(self.inpatient.name, to_bed.name, self.request.name)

        task = self._latest_task()
        self.assertIsNotNone(task)
        self.assertEqual(task.task_type, "DISCHARGE_CLEAN")
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(self._state(), "CLEANING_REQUIRED")

    def test_start_moves_bed_to_cleaning_in_progress(self):
        self._cleaning_bed()
        task = housekeeping.ensure_task(self.bed.name)

        housekeeping.start_task(task.name)

        task.reload()
        self.assertEqual(task.status, "IN_PROGRESS")
        self.assertIsNotNone(task.started_at)
        self.assertEqual(self._state(), "CLEANING_IN_PROGRESS")

    def test_complete_keeps_bed_in_cleaning(self):
        self._cleaning_bed()
        task = housekeeping.ensure_task(self.bed.name)
        housekeeping.start_task(task.name)

        housekeeping.complete_task(task.name)

        task.reload()
        self.assertEqual(task.status, "COMPLETED")
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(self._state(), "CLEANING_IN_PROGRESS")

    def test_verify_releases_bed_to_ready(self):
        self._cleaning_bed()
        task = housekeeping.ensure_task(self.bed.name)
        housekeeping.start_task(task.name)
        housekeeping.complete_task(task.name)

        housekeeping.verify_task(task.name)

        task.reload()
        self.assertEqual(task.status, "VERIFIED")
        self.assertEqual(task.verified_by, "Administrator")
        self.assertEqual(self._state(), "READY")

    def test_direct_ready_is_blocked_without_verified_task(self):
        self._cleaning_bed()
        housekeeping.ensure_task(self.bed.name)

        with self.assertRaises(frappe.ValidationError):
            operational.mark_ready(self.bed.name)

        self.assertEqual(self._state(), "CLEANING_REQUIRED")

    def test_ready_is_allowed_after_verification(self):
        self._cleaning_bed()
        task = housekeeping.ensure_task(self.bed.name)
        housekeeping.start_task(task.name)
        housekeeping.complete_task(task.name)
        housekeeping.verify_task(task.name)

        self.assertEqual(self._state(), "READY")

    def test_isolation_task_uses_isolation_state(self):
        operational.set_state(self.bed.name, "OCCUPIED", reason="test occupancy")
        operational.mark_vacated(self.bed.name, reason="test vacate")
        operational.start_isolation_cleaning(
            self.bed.name, reason="isolation precaution"
        )

        task = housekeeping.ensure_task(self.bed.name)
        self.assertEqual(task.task_type, "ISOLATION_CLEAN")
        housekeeping.start_task(task.name)
        self.assertEqual(self._state(), "ISOLATION_CLEANING")
        housekeeping.complete_task(task.name)
        housekeeping.verify_task(task.name)
        self.assertEqual(self._state(), "READY")

    def test_cancel_leaves_bed_in_cleaning(self):
        self._cleaning_bed()
        task = housekeeping.ensure_task(self.bed.name)
        housekeeping.start_task(task.name)

        housekeeping.cancel_task(task.name, reason="cleaning cancelled")

        task.reload()
        self.assertEqual(task.status, "CANCELLED")
        self.assertEqual(self._state(), "CLEANING_IN_PROGRESS")

    def test_ensure_task_is_idempotent(self):
        self._cleaning_bed()

        first = housekeeping.ensure_task(self.bed.name)
        second = housekeeping.ensure_task(self.bed.name)

        self.assertEqual(first.name, second.name)
        self.assertEqual(
            frappe.db.count(
                "Housekeeping Task",
                {"healthcare_service_unit": self.bed.name},
            ),
            1,
        )

    def test_reconcile_creates_missing_task(self):
        self._cleaning_bed()

        result = housekeeping.reconcile_cleaning_tasks()

        task = self._latest_task()
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "PENDING")
        self.assertIn(self.bed.name, result["created"])
