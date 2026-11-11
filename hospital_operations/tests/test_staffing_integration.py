import unittest
from datetime import datetime, time

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
    from frappe.utils import getdate, today
except ImportError:
    raise unittest.SkipTest("Frappe bench required to run staffing integration tests")

from hospital_operations.bed_allocation import staffing_service
from hospital_operations.integrations import marley
from hospital_operations.tests import factories


def staffing_policy(**overrides):
    staffing = {
        "ratio": {"ICU": 2},
        "competency": {"ICU": "ICU"},
        "minimum_nurses": {"ICU": 1},
        "presence_source": "attendance",
        "count_half_day": False,
        "count_work_from_home": True,
    }
    staffing.update(overrides)
    return factories.make_policy(staffing=staffing)


def at_time(hour, minute=0, day=None):
    return datetime.combine(getdate(day or today()), time(hour, minute))


class TestStaffingService(FrappeTestCase):
    def setUp(self):
        self.company = factories.get_company()
        self.unit_type = factories.make_service_unit_type()
        self.ward = factories.make_ward(self.company)
        self.bed = factories.make_bed(self.unit_type, "ICU", self.ward, self.company)
        factories.make_bed(self.unit_type, "ICU", self.ward, self.company)
        self.shift = factories.make_shift_type("09:00:00", "17:00:00")
        self.at = at_time(10)

    def _present_nurse(self, competency="ICU", ward=None):
        employee = factories.make_employee(self.company)
        factories.make_ward_nurse_assignment(
            employee, ward or self.ward, competency=competency
        )
        factories.make_shift_assignment(employee, self.shift)
        factories.make_attendance(employee, "Present")
        return employee

    def test_present_qualified_nurse_produces_capacity(self):
        self._present_nurse()
        policy = staffing_policy()

        result = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertTrue(result.enabled)
        self.assertEqual(result.available_nurses, 1)
        self.assertEqual(result.qualified_nurses, 1)
        self.assertEqual(result.staffed_bed_capacity, 2)
        self.assertEqual(result.available_capacity, 2)
        self.assertEqual(result.policy_version, policy.version)

    def test_approved_leave_excludes_nurse(self):
        employee = self._present_nurse()
        factories.make_leave_application(employee, from_date=today(), to_date=today())
        policy = staffing_policy()

        result = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertEqual(result.available_nurses, 0)
        self.assertEqual(result.staffed_bed_capacity, 0)
        self.assertEqual(result.exclusions.get("ON_LEAVE"), 1)

    def test_absent_nurse_is_excluded(self):
        employee = self._present_nurse()
        factories.make_attendance(employee, "Absent")
        policy = staffing_policy()

        result = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertEqual(result.available_nurses, 0)
        self.assertEqual(result.exclusions.get("ABSENT"), 1)

    def test_wrong_competency_is_not_qualified(self):
        self._present_nurse(competency="GENERAL")
        policy = staffing_policy()

        result = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertEqual(result.available_nurses, 1)
        self.assertEqual(result.qualified_nurses, 0)
        self.assertEqual(result.staffed_bed_capacity, 0)
        self.assertEqual(result.exclusions.get("WRONG_COMPETENCY"), 1)

    def test_nurse_assigned_to_another_ward_is_not_counted(self):
        other_ward = factories.make_ward(self.company)
        self._present_nurse(ward=other_ward)
        policy = staffing_policy()

        result = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertEqual(result.available_nurses, 0)

    def test_shift_transition_changes_counted_nurses(self):
        night = factories.make_shift_type("20:00:00", "08:00:00")
        day_nurse = factories.make_employee(self.company)
        night_nurse = factories.make_employee(self.company)
        for employee, shift in ((day_nurse, self.shift), (night_nurse, night)):
            factories.make_ward_nurse_assignment(employee, self.ward, competency="ICU")
            factories.make_shift_assignment(employee, shift)
            factories.make_attendance(employee, "Present")
        policy = staffing_policy()

        day_result = staffing_service.ward_capacity(
            self.ward.name, at=at_time(10), policy=policy
        )
        night_result = staffing_service.ward_capacity(
            self.ward.name, at=at_time(21), policy=policy
        )

        self.assertEqual(day_result.available_nurses, 1)
        self.assertEqual(night_result.available_nurses, 1)

    def test_checkin_presence_source(self):
        employee = self._present_nurse()
        policy = staffing_policy(presence_source="checkin")
        factories.make_checkin(employee, "IN", time=at_time(9, 5))

        present = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )
        factories.make_checkin(employee, "OUT", time=at_time(12))
        absent = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertEqual(present.available_nurses, 1)
        self.assertEqual(absent.available_nurses, 0)

    def test_current_patients_reduce_available_capacity(self):
        self._present_nurse()
        policy = staffing_policy()
        patient = factories.make_patient()
        inpatient = factories.make_inpatient_record(
            patient, self.unit_type, self.company
        )
        marley.admit(inpatient.name, self.bed.name)

        result = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertEqual(result.current_patients, 1)
        self.assertEqual(result.staffed_bed_capacity, 2)
        self.assertEqual(result.available_capacity, 1)

    def test_refresh_snapshot_persists_derived_counts(self):
        self._present_nurse()
        policy = staffing_policy()

        snapshot = staffing_service.refresh_ward_snapshot(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertIsNotNone(snapshot.name)
        stored = frappe.get_doc("Bed Staffing Snapshot", snapshot.name)
        self.assertEqual(stored.ward, self.ward.name)
        self.assertEqual(stored.shift, self.shift.name)
        self.assertEqual(stored.available_nurses, 1)
        self.assertEqual(stored.qualified_nurses, 1)
        self.assertEqual(stored.staffed_bed_capacity, 2)
        self.assertEqual(stored.policy_version, policy.version)

    def test_missing_staffing_policy_disables_capacity_checks(self):
        self._present_nurse()
        policy = factories.make_policy()

        result = staffing_service.ward_capacity(
            self.ward.name, at=self.at, policy=policy
        )

        self.assertFalse(result.enabled)
        availability = staffing_service.staffing_availability(
            [self.ward.name], at=self.at, policy=policy
        )
        self.assertIsNone(availability[self.ward.name])
