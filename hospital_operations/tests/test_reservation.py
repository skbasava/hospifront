import unittest
from unittest.mock import patch

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
except ImportError:
    raise unittest.SkipTest("Frappe bench required to run reservation tests")

from hospital_operations.bed_allocation import operational, reservation
from hospital_operations.bed_allocation.transaction import StaleProposal
from hospital_operations.integrations import marley
from hospital_operations.tests import factories


class TestReservation(FrappeTestCase):
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

    def _bed_state(self, bed=None):
        return operational.get_state_value((bed or self.bed).name)

    def _reservation_rows(self, request=None):
        return frappe.get_all(
            "Bed Reservation",
            filters={"allocation_request": (request or self.request).name},
            fields=["name", "status", "reservation_type"],
        )

    def _make_competing_request(self):
        patient = factories.make_patient()
        inpatient = factories.make_inpatient_record(
            patient, self.unit_type, self.company
        )
        request = factories.make_request(patient, inpatient, company=self.company)
        request.status = "PROPOSED"
        request.save(ignore_permissions=True)
        proposal = factories.make_proposal_for_bed(self.bed, request, self.allocation_run)
        return request, inpatient, proposal

    def test_confirm_admits_and_occupies(self):
        result = reservation.confirm(self.proposal.name)

        self.assertFalse(result["idempotent"])
        self.assertEqual(result["status"], "CONFIRMED")
        self.inpatient.reload()
        self.assertEqual(self.inpatient.status, "Admitted")
        self.assertEqual(marley.get_active_bed(self.inpatient.name), self.bed.name)
        self.assertEqual(self._bed_state(), "OCCUPIED")
        rows = self._reservation_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "CONFIRMED")
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Request", self.request.name, "status"
            ),
            "ALLOCATED",
        )
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Proposal", self.proposal.name, "status"
            ),
            "CONFIRMED",
        )

    def test_duplicate_confirm_is_idempotent(self):
        first = reservation.confirm(self.proposal.name)
        second = reservation.confirm(self.proposal.name)

        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(len(self._reservation_rows()), 1)
        self.assertEqual(len(marley.get_active_occupancies(self.inpatient.name)), 1)

    def test_second_claim_for_same_bed_is_rejected(self):
        request, _, proposal = self._make_competing_request()
        reservation.confirm(self.proposal.name)

        with self.assertRaises(StaleProposal):
            reservation.confirm(proposal.name)

        self.assertEqual(self._bed_state(), "OCCUPIED")
        self.assertEqual(len(self._reservation_rows(request)), 0)

    def test_stale_proposal_is_rejected(self):
        version = self.proposal.bed_state_version
        operational.mark_reserved(
            self.bed.name,
            reason="test reservation",
            expected_version=version,
        )
        operational.mark_ready(self.bed.name, reason="test reset")

        with self.assertRaises(StaleProposal):
            reservation.confirm(self.proposal.name)

        self.assertEqual(self._bed_state(), "READY")
        self.assertEqual(len(self._reservation_rows()), 0)

    def test_failed_admission_rolls_back(self):
        with patch(
            "hospital_operations.bed_allocation.reservation.marley.admit",
            side_effect=marley.MarleyError("synthetic Marley failure"),
        ):
            with self.assertRaises(marley.MarleyError):
                reservation.confirm(self.proposal.name)

        self.assertEqual(self._bed_state(), "READY")
        self.assertEqual(len(self._reservation_rows()), 0)
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Request", self.request.name, "status"
            ),
            "PROPOSED",
        )
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Proposal", self.proposal.name, "status"
            ),
            "PROPOSED",
        )

    def test_expire_releases_reserved_bed(self):
        active = factories.make_active_reservation(
            self.request, self.bed, self.inpatient, expired=True
        )
        operational.mark_reserved(self.bed.name, reason="test reservation")

        result = reservation.expire()

        self.assertEqual(result["expired"], 1)
        active.reload()
        self.assertEqual(active.status, "EXPIRED")
        self.assertEqual(self._bed_state(), "READY")
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Request", self.request.name, "status"
            ),
            "WAITLISTED",
        )

    def test_cancel_releases_reserved_bed(self):
        active = factories.make_active_reservation(
            self.request, self.bed, self.inpatient
        )
        operational.mark_reserved(self.bed.name, reason="test reservation")

        result = reservation.cancel(self.proposal.name, reason="test cancel")

        self.assertFalse(result["idempotent"])
        active.reload()
        self.assertEqual(active.status, "CANCELLED")
        self.assertEqual(self._bed_state(), "READY")
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Proposal", self.proposal.name, "status"
            ),
            "CANCELLED",
        )

    def test_transfer_moves_occupancy_and_cleans_old_bed(self):
        reservation.confirm(self.proposal.name)
        to_bed = factories.make_bed(
            self.unit_type, "ICU", self.ward, self.company
        )

        result = reservation.transfer(
            self.inpatient.name, to_bed.name, self.request.name
        )

        self.assertEqual(result["from_bed"], self.bed.name)
        self.assertEqual(result["to_bed"], to_bed.name)
        self.assertEqual(marley.get_active_bed(self.inpatient.name), to_bed.name)
        self.assertEqual(self._bed_state(self.bed), "CLEANING_REQUIRED")
        self.assertEqual(self._bed_state(to_bed), "OCCUPIED")
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Request", self.request.name, "status"
            ),
            "ALLOCATED",
        )

    def test_discharge_releases_bed_to_cleaning(self):
        reservation.confirm(self.proposal.name)

        result = reservation.discharge(self.inpatient.name)
        second = reservation.discharge(self.inpatient.name)

        self.assertEqual(result["released_beds"], [self.bed.name])
        self.assertTrue(second["idempotent"])
        self.inpatient.reload()
        self.assertEqual(self.inpatient.status, "Discharged")
        self.assertEqual(marley.get_active_occupancies(self.inpatient.name), [])
        self.assertEqual(self._bed_state(), "CLEANING_REQUIRED")

    def test_reconcile_releases_orphaned_reserved_state(self):
        operational.mark_reserved(self.bed.name, reason="test reservation")

        result = reservation.reconcile()

        self.assertEqual(result["released_beds"], 1)
        self.assertEqual(self._bed_state(), "READY")

    def test_expired_reservation_is_not_reused_by_key(self):
        active = factories.make_active_reservation(
            self.request, self.bed, self.inpatient, key="shared-key", expired=True
        )
        operational.mark_reserved(self.bed.name, reason="test reservation")
        reservation.expire()

        active.reload()
        self.assertEqual(active.status, "EXPIRED")
        self.assertEqual(
            frappe.db.exists(
                "Bed Reservation", {"idempotency_key": "shared-key"}
            ),
            active.name,
        )
