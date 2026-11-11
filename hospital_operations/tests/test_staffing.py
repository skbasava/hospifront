import unittest
from datetime import datetime, time

from hospital_operations.bed_allocation.staffing import (
    NurseSignal,
    StaffingRulesError,
    compute,
    is_within_shift,
    parse_rules,
    resolve_presence,
)


def rules(**overrides):
    staffing = {
        "ratio": {"ICU": 2, "General": 6, "Special": 4, "Other": 6},
        "competency": {"ICU": "ICU", "General": "GENERAL"},
        "minimum_nurses": {"ICU": 2},
        "presence_source": "attendance",
        "count_half_day": False,
        "count_work_from_home": True,
    }
    staffing.update(overrides)
    return parse_rules({"staffing": staffing})


def nurse(employee, **overrides):
    values = {
        "employee": employee,
        "ward": "ICU-A",
        "competencies": frozenset({"ICU"}),
        "active": True,
        "on_leave": False,
        "on_shift": True,
        "present": True,
        "attendance_status": "Present",
        "shift_type": "Day",
    }
    values.update(overrides)
    return NurseSignal(**values)


class TestStaffingRules(unittest.TestCase):
    def test_nested_staffing_rules_are_parsed(self):
        parsed = rules()

        self.assertEqual(parsed.patients_per_nurse("ICU"), 2)
        self.assertEqual(parsed.required_competency("ICU"), "ICU")
        self.assertEqual(parsed.minimum_for("ICU"), 2)
        self.assertEqual(parsed.presence_source, "attendance")
        self.assertFalse(parsed.count_half_day)
        self.assertTrue(parsed.count_work_from_home)

    def test_missing_or_empty_staffing_config_is_disabled(self):
        self.assertIsNone(parse_rules(None))
        self.assertIsNone(parse_rules({}))
        self.assertIsNone(parse_rules({"care_level_compatibility": {}}))
        self.assertIsNone(parse_rules({"staffing": {}}))

    def test_invalid_presence_source_is_rejected(self):
        with self.assertRaises(StaffingRulesError):
            rules(presence_source="crystal_ball")

    def test_invalid_ratio_is_rejected(self):
        with self.assertRaises(StaffingRulesError):
            rules(ratio={"ICU": 0})

    def test_attendance_presence_respects_policy_flags(self):
        parsed = rules(count_half_day=False, count_work_from_home=True)

        self.assertTrue(resolve_presence(parsed, "Present"))
        self.assertTrue(resolve_presence(parsed, "Work From Home"))
        self.assertFalse(resolve_presence(parsed, "Half Day"))
        self.assertFalse(resolve_presence(parsed, "Absent"))
        self.assertFalse(resolve_presence(parsed, "On Leave"))
        self.assertFalse(resolve_presence(parsed, None))

        counted = rules(count_half_day=True, count_work_from_home=False)
        self.assertTrue(resolve_presence(counted, "Half Day"))
        self.assertFalse(resolve_presence(counted, "Work From Home"))

    def test_scheduled_presence_source_is_explicit(self):
        parsed = rules(presence_source="scheduled")

        self.assertTrue(resolve_presence(parsed, None))


class TestShiftWindows(unittest.TestCase):
    def test_day_shift_boundaries(self):
        start, end = time(9, 0), time(17, 0)

        self.assertTrue(is_within_shift(start, end, datetime(2026, 1, 5, 9, 0)))
        self.assertTrue(is_within_shift(start, end, datetime(2026, 1, 5, 16, 59)))
        self.assertFalse(is_within_shift(start, end, datetime(2026, 1, 5, 8, 59)))
        self.assertFalse(is_within_shift(start, end, datetime(2026, 1, 5, 17, 0)))

    def test_overnight_shift_wraps_into_next_day(self):
        start, end = time(20, 0), time(8, 0)

        self.assertTrue(is_within_shift(start, end, datetime(2026, 1, 5, 23, 0)))
        self.assertTrue(is_within_shift(start, end, datetime(2026, 1, 6, 2, 0)))
        self.assertFalse(is_within_shift(start, end, datetime(2026, 1, 5, 19, 59)))
        self.assertFalse(is_within_shift(start, end, datetime(2026, 1, 6, 8, 0)))


class TestStaffingCapacity(unittest.TestCase):
    def test_ratio_caps_staffed_capacity(self):
        result = compute(
            rules(),
            ward="ICU-A",
            care_level="ICU",
            nurses=[nurse("E1"), nurse("E2"), nurse("E3")],
            current_patients=2,
            physical_beds=10,
            shift="Day",
            policy_version="1",
        )

        self.assertTrue(result.enabled)
        self.assertEqual(result.available_nurses, 3)
        self.assertEqual(result.qualified_nurses, 3)
        self.assertEqual(result.staffed_bed_capacity, 6)
        self.assertEqual(result.available_capacity, 4)
        self.assertEqual(result.shift, "Day")

    def test_approved_leave_removes_nurse(self):
        result = compute(
            rules(),
            ward="ICU-A",
            care_level="ICU",
            nurses=[
                nurse("E1"),
                nurse("E2", on_leave=True, present=False, attendance_status="On Leave"),
                nurse("E3"),
            ],
            current_patients=0,
            physical_beds=10,
        )

        self.assertEqual(result.available_nurses, 2)
        self.assertEqual(result.qualified_nurses, 2)
        self.assertEqual(result.staffed_bed_capacity, 4)
        self.assertEqual(result.exclusions.get("ON_LEAVE"), 1)

    def test_absent_nurse_is_excluded(self):
        result = compute(
            rules(),
            ward="ICU-A",
            care_level="ICU",
            nurses=[nurse("E1"), nurse("E2", present=False, attendance_status="Absent")],
            current_patients=0,
            physical_beds=10,
        )

        self.assertEqual(result.available_nurses, 1)
        self.assertEqual(result.exclusions.get("ABSENT"), 1)

    def test_off_shift_and_inactive_nurses_are_excluded(self):
        result = compute(
            rules(),
            ward="ICU-A",
            care_level="ICU",
            nurses=[
                nurse("E1"),
                nurse("E2", on_shift=False),
                nurse("E3", active=False),
            ],
            current_patients=0,
            physical_beds=10,
        )

        self.assertEqual(result.available_nurses, 1)
        self.assertEqual(result.exclusions.get("OFF_SHIFT"), 1)
        self.assertEqual(result.exclusions.get("INACTIVE_EMPLOYEE"), 1)

    def test_wrong_competency_is_not_qualified(self):
        result = compute(
            rules(),
            ward="ICU-A",
            care_level="ICU",
            nurses=[
                nurse("E1"),
                nurse("E2"),
                nurse("E3", competencies=frozenset({"GENERAL"})),
            ],
            current_patients=0,
            physical_beds=10,
        )

        self.assertEqual(result.available_nurses, 3)
        self.assertEqual(result.qualified_nurses, 2)
        self.assertEqual(result.staffed_bed_capacity, 4)
        self.assertEqual(result.exclusions.get("WRONG_COMPETENCY"), 1)

    def test_wrong_ward_is_excluded(self):
        result = compute(
            rules(),
            ward="ICU-A",
            care_level="ICU",
            nurses=[nurse("E1", ward="ICU-B")],
            current_patients=0,
            physical_beds=10,
        )

        self.assertEqual(result.available_nurses, 0)
        self.assertEqual(result.staffed_bed_capacity, 0)
        self.assertEqual(result.exclusions.get("WRONG_WARD"), 1)

    def test_minimum_nurses_can_zero_capacity(self):
        result = compute(
            rules(minimum_nurses={"ICU": 2}),
            ward="ICU-A",
            care_level="ICU",
            nurses=[nurse("E1")],
            current_patients=0,
            physical_beds=10,
        )

        self.assertEqual(result.qualified_nurses, 1)
        self.assertEqual(result.staffed_bed_capacity, 0)
        self.assertEqual(result.available_capacity, 0)
        self.assertTrue(result.notes.get("below_minimum_nurses"))

    def test_physical_beds_cap_capacity(self):
        result = compute(
            rules(),
            ward="ICU-A",
            care_level="ICU",
            nurses=[nurse("E1"), nurse("E2"), nurse("E3")],
            current_patients=0,
            physical_beds=5,
        )

        self.assertEqual(result.staffed_bed_capacity, 5)

    def test_occupied_patients_reduce_available_capacity(self):
        result = compute(
            rules(),
            ward="ICU-A",
            care_level="ICU",
            nurses=[nurse("E1"), nurse("E2")],
            current_patients=3,
            physical_beds=10,
        )

        self.assertEqual(result.staffed_bed_capacity, 4)
        self.assertEqual(result.available_capacity, 1)


if __name__ == "__main__":
    unittest.main()
