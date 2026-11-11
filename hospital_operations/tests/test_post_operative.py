import unittest

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
    from frappe.utils import add_to_date, now_datetime
except ImportError:
    raise unittest.SkipTest("Frappe bench required to run post-operative tests")

from hospital_operations.bed_allocation import operational, post_operative
from hospital_operations.bed_allocation.transaction import ReservationConflict
from hospital_operations.integrations import marley
from hospital_operations.tests import factories


class TestPostOperativeReservations(FrappeTestCase):
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
            self.patient,
            self.inpatient,
            care_level="ICU",
            priority="Urgent",
            source="OT",
            company=self.company,
        )

    def _window(self, start_minutes=60, duration_minutes=120):
        start = add_to_date(now_datetime(), minutes=start_minutes)
        return start, add_to_date(start, minutes=duration_minutes)

    def _create(self, start_minutes=60, duration_minutes=120, key=None, request=None):
        start, expires = self._window(start_minutes, duration_minutes)
        return post_operative.create_reservation(
            (request or self.request).name,
            self.bed.name,
            start_at=start,
            expires_at=expires,
            idempotency_key=key,
        )

    def _state(self, bed=None):
        return operational.get_state_value((bed or self.bed).name)

    def test_create_reserves_bed_and_request(self):
        result = self._create()

        reservation = frappe.get_doc("Bed Reservation", result["reservation"])
        self.assertEqual(reservation.reservation_type, "POST_OPERATIVE")
        self.assertEqual(reservation.status, "ACTIVE")
        self.assertEqual(reservation.start_at, result["start_at"])
        self.assertEqual(reservation.expires_at, result["expires_at"])
        self.assertEqual(self._state(), "RESERVED")
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Request", self.request.name, "status"
            ),
            "RESERVED",
        )

    def test_create_is_idempotent_for_same_key(self):
        start, expires = self._window()
        first = post_operative.create_reservation(
            self.request.name,
            self.bed.name,
            start_at=start,
            expires_at=expires,
            idempotency_key="postop-idempotent",
        )
        second = post_operative.create_reservation(
            self.request.name,
            self.bed.name,
            start_at=start,
            expires_at=expires,
            idempotency_key="postop-idempotent",
        )

        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["reservation"], second["reservation"])
        self.assertEqual(
            frappe.db.count(
                "Bed Reservation",
                {"idempotency_key": "postop-idempotent"},
            ),
            1,
        )

    def test_create_rejects_overlapping_active_hold(self):
        start, expires = self._window(60, 180)
        other_patient = factories.make_patient()
        other_inpatient = factories.make_inpatient_record(
            other_patient, self.unit_type, self.company
        )
        other_request = factories.make_request(
            other_patient,
            other_inpatient,
            source="OT",
            company=self.company,
        )
        factories.make_postop_reservation(
            other_request,
            self.bed,
            other_inpatient,
            start_at=start,
            expires_at=expires,
        )

        with self.assertRaises(ReservationConflict):
            self._create(120, 180)

        self.assertEqual(
            frappe.db.count(
                "Bed Reservation",
                {
                    "healthcare_service_unit": self.bed.name,
                    "reservation_type": "POST_OPERATIVE",
                    "status": "ACTIVE",
                },
            ),
            1,
        )

    def test_create_rejects_bed_that_requires_cleaning(self):
        cleaning_bed = factories.make_bed(
            self.unit_type, "ICU", self.ward, self.company, state="CLEANING_REQUIRED"
        )
        start, expires = self._window()

        with self.assertRaises(ReservationConflict):
            post_operative.create_reservation(
                self.request.name,
                cleaning_bed.name,
                start_at=start,
                expires_at=expires,
            )

        self.assertEqual(self._state(cleaning_bed), "CLEANING_REQUIRED")

    def test_create_rejects_non_ot_request(self):
        request = factories.make_request(
            self.patient,
            self.inpatient,
            care_level="ICU",
            source="Direct Admission",
            company=self.company,
        )

        with self.assertRaises(post_operative.PostOperativeError):
            self._create(request=request)

    def test_reschedule_updates_window_and_checks_conflicts(self):
        reservation = self._create(120, 120, key="reschedule-me")["reservation"]
        blocker_start, blocker_end = self._window(300, 180)
        other_patient = factories.make_patient()
        other_inpatient = factories.make_inpatient_record(
            other_patient, self.unit_type, self.company
        )
        other_request = factories.make_request(
            other_patient,
            other_inpatient,
            source="OT",
            company=self.company,
        )
        factories.make_postop_reservation(
            other_request,
            self.bed,
            other_inpatient,
            start_at=blocker_start,
            expires_at=blocker_end,
        )

        with self.assertRaises(ReservationConflict):
            post_operative.reschedule(
                reservation,
                start_at=add_to_date(blocker_start, minutes=30),
                expires_at=add_to_date(blocker_start, minutes=150),
            )

        new_start, new_end = self._window(600, 90)
        result = post_operative.reschedule(
            reservation, start_at=new_start, expires_at=new_end
        )
        self.assertEqual(result["status"], "ACTIVE")
        doc = frappe.get_doc("Bed Reservation", reservation)
        self.assertEqual(doc.start_at, new_start)
        self.assertEqual(doc.expires_at, new_end)
        self.assertEqual(self._state(), "RESERVED")

    def test_cancel_releases_bed_and_cancels_request(self):
        reservation = self._create(key="cancel-me")["reservation"]

        first = post_operative.cancel(reservation, reason="surgery cancelled")
        second = post_operative.cancel(reservation, reason="surgery cancelled")

        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(self._state(), "READY")
        self.assertEqual(
            frappe.db.get_value("Bed Reservation", reservation, "status"),
            "CANCELLED",
        )
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Request", self.request.name, "status"
            ),
            "CANCELLED",
        )

    def test_preempt_requires_emergency_and_clinical_approval(self):
        reservation = self._create(key="preempt-me")["reservation"]
        routine = factories.make_request(
            factories.make_patient(),
            self.inpatient,
            priority="Routine",
            source="ED",
            company=self.company,
        )
        emergency_patient = factories.make_patient()
        emergency_inpatient = factories.make_inpatient_record(
            emergency_patient, self.unit_type, self.company
        )
        emergency = factories.make_request(
            emergency_patient,
            emergency_inpatient,
            priority="Emergency",
            source="ED",
            company=self.company,
        )

        with self.assertRaises(post_operative.PostOperativeError):
            post_operative.preempt(
                reservation, routine.name, clinical_approval_reference="APPROVED"
            )
        with self.assertRaises(post_operative.PostOperativeError):
            post_operative.preempt(reservation, emergency.name)

        result = post_operative.preempt(
            reservation,
            emergency.name,
            clinical_approval_reference="CLINICAL-APPROVAL-1",
            reason="Emergency ICU patient",
        )

        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(self._state(), "READY")
        self.assertEqual(
            frappe.db.get_value("Bed Reservation", reservation, "status"),
            "CANCELLED",
        )
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Request", self.request.name, "status"
            ),
            "WAITLISTED",
        )
        self.assertTrue(
            frappe.db.exists(
                "Bed Allocation Event",
                {"event_type": "MANUAL_OVERRIDE", "correlation_id": reservation},
            )
        )

    def test_confirm_admits_scheduled_patient(self):
        reservation = self._create(-30, 120, key="confirm-admit")["reservation"]

        first = post_operative.confirm(reservation)
        second = post_operative.confirm(reservation)

        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.inpatient.reload()
        self.assertEqual(self.inpatient.status, "Admitted")
        self.assertEqual(marley.get_active_bed(self.inpatient.name), self.bed.name)
        self.assertEqual(self._state(), "OCCUPIED")
        self.assertEqual(
            frappe.db.get_value("Bed Reservation", reservation, "status"),
            "CONFIRMED",
        )
        self.assertEqual(
            frappe.db.get_value(
                "Bed Allocation Request", self.request.name, "status"
            ),
            "ALLOCATED",
        )

    def test_confirm_transfers_admitted_patient_and_cleans_old_bed(self):
        current_bed = factories.make_bed(
            self.unit_type, "ICU", self.ward, self.company
        )
        marley.admit(self.inpatient.name, current_bed.name)
        operational.mark_occupied(current_bed.name, reason="test admission")
        reservation = self._create(-30, 120, key="confirm-transfer")["reservation"]

        result = post_operative.confirm(reservation)

        self.assertEqual(result["status"], "CONFIRMED")
        self.assertEqual(marley.get_active_bed(self.inpatient.name), self.bed.name)
        self.assertEqual(self._state(current_bed), "CLEANING_REQUIRED")
        self.assertEqual(self._state(), "OCCUPIED")
        self.assertTrue(
            frappe.db.exists(
                "Housekeeping Task",
                {
                    "healthcare_service_unit": current_bed.name,
                    "status": "PENDING",
                },
            )
        )

    def test_confirm_rejects_bed_blocked_after_hold(self):
        reservation = self._create(-30, 120, key="blocked-hold")["reservation"]
        operational.block_bed(self.bed.name, reason="emergency maintenance")

        with self.assertRaises(ReservationConflict):
            post_operative.confirm(reservation)

        self.assertEqual(self._state(), "BLOCKED")
        self.assertEqual(
            frappe.db.get_value("Bed Reservation", reservation, "status"),
            "ACTIVE",
        )

    def test_confirm_rejects_expired_hold(self):
        reservation = self._create(60, 120, key="expired-hold")["reservation"]
        frappe.db.set_value(
            "Bed Reservation",
            reservation,
            "expires_at",
            add_to_date(now_datetime(), minutes=-1),
        )

        with self.assertRaises(ReservationConflict):
            post_operative.confirm(reservation)

        self.assertEqual(self._state(), "RESERVED")
