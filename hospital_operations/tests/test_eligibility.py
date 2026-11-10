import unittest

from hospital_operations.bed_allocation.eligibility import (
    BedContext,
    ClinicalRequirement,
    RequestContext,
    evaluate,
)


def requirement(**overrides):
    values = {
        "requirement_type": "VENTILATOR",
        "required_value": "1",
        "operator": "=",
        "mandatory": True,
    }
    values.update(overrides)
    return ClinicalRequirement(**values)


def request(**overrides):
    values = {
        "name": "BAR-0001",
        "care_level": "ICU",
        "priority": "Routine",
        "medical_department": None,
        "requirements": (),
    }
    values.update(overrides)
    return RequestContext(**values)


def bed(**overrides):
    values = {
        "name": "ICU-BED-01",
        "care_level": "ICU",
        "state": "READY",
        "allocatable": True,
        "occupied": False,
        "active_reservation": False,
        "capabilities": {},
        "specialties": frozenset(),
        "room_sharing_ok": True,
        "staffed_capacity_available": None,
    }
    values.update(overrides)
    return BedContext(**values)


def codes(result):
    return {reason.code for reason in result.reasons}


class TestEligibility(unittest.TestCase):
    def test_compatible_icu_bed_is_eligible(self):
        result = evaluate(request(), bed())
        self.assertTrue(result.eligible)
        self.assertEqual(result.reasons, [])
        self.assertEqual(result.request, "BAR-0001")
        self.assertEqual(result.bed, "ICU-BED-01")

    def test_icu_request_rejects_general_bed(self):
        result = evaluate(request(care_level="ICU"), bed(care_level="General"))
        self.assertFalse(result.eligible)
        self.assertIn("CARE_LEVEL_MISMATCH", codes(result))

    def test_general_request_rejects_icu_bed(self):
        result = evaluate(request(care_level="General"), bed(care_level="ICU"))
        self.assertFalse(result.eligible)
        self.assertIn("CARE_LEVEL_MISMATCH", codes(result))

    def test_other_care_level_accepts_any_bed(self):
        result = evaluate(request(care_level="Other"), bed(care_level="Special"))
        self.assertTrue(result.eligible)

    def test_maintenance_bed_is_never_allocatable(self):
        result = evaluate(request(), bed(state="MAINTENANCE"))
        self.assertFalse(result.eligible)
        self.assertIn("BED_MAINTENANCE", codes(result))

    def test_cleaning_and_vacated_beds_are_rejected(self):
        for state in ("VACATED", "CLEANING_REQUIRED", "CLEANING_IN_PROGRESS"):
            result = evaluate(request(), bed(state=state))
            self.assertFalse(result.eligible, msg=state)

    def test_blocked_bed_is_rejected(self):
        result = evaluate(request(), bed(state="BLOCKED"))
        self.assertIn("BED_BLOCKED", codes(result))

    def test_occupied_and_reserved_flags_are_rejected(self):
        self.assertIn("BED_OCCUPIED", codes(evaluate(request(), bed(occupied=True))))
        self.assertIn(
            "BED_ACTIVE_RESERVATION",
            codes(evaluate(request(), bed(active_reservation=True))),
        )

    def test_non_allocatable_bed_is_rejected(self):
        result = evaluate(request(), bed(allocatable=False))
        self.assertIn("BED_NOT_ALLOCATABLE", codes(result))

    def test_unknown_state_is_rejected(self):
        result = evaluate(request(), bed(state="UNKNOWN"))
        self.assertIn("BED_STATE_NOT_ALLOCATABLE", codes(result))

    def test_ventilator_requirement(self):
        needed = request(requirements=(requirement(),))
        self.assertIn("REQUIREMENT_NOT_MET", codes(evaluate(needed, bed())))
        capable = bed(capabilities={"VENTILATOR": frozenset({"1"})})
        result = evaluate(needed, capable)
        self.assertTrue(result.eligible)
        self.assertIn("VENTILATOR", result.matched_requirements)

    def test_optional_requirement_does_not_block(self):
        optional = requirement(requirement_type="OXYGEN", mandatory=False)
        result = evaluate(request(requirements=(optional,)), bed())
        self.assertTrue(result.eligible)
        self.assertEqual(len(result.optional_gaps), 1)
        self.assertEqual(result.optional_gaps[0].code, "OPTIONAL_REQUIREMENT_NOT_MET")

    def test_isolation_accepts_specific_isolation_capability(self):
        isolation = requirement(requirement_type="ISOLATION")
        capable = bed(capabilities={"ISOLATION": frozenset({"negative_pressure"})})
        self.assertTrue(evaluate(request(requirements=(isolation,)), capable).eligible)

    def test_isolation_zero_capability_is_rejected(self):
        isolation = requirement(requirement_type="ISOLATION")
        incapable = bed(capabilities={"ISOLATION": frozenset({"0"})})
        result = evaluate(request(requirements=(isolation,)), incapable)
        self.assertIn("REQUIREMENT_NOT_MET", codes(result))

    def test_specialty_match_and_mismatch(self):
        cardiology = request(medical_department="Cardiology")
        matching = bed(specialties=frozenset({"Cardiology"}))
        self.assertTrue(evaluate(cardiology, matching).eligible)
        mismatch = bed(specialties=frozenset({"Orthopedics"}))
        self.assertIn(
            "SPECIALTY_MISMATCH", codes(evaluate(cardiology, mismatch))
        )

    def test_specialty_requirement_in_operator(self):
        specialty = requirement(
            requirement_type="SPECIALTY",
            operator="IN",
            required_value="Cardiology,Neurology",
        )
        capable = bed(specialties=frozenset({"Neurology"}))
        self.assertTrue(evaluate(request(requirements=(specialty,)), capable).eligible)

    def test_not_in_operator(self):
        specialty = requirement(
            requirement_type="SPECIALTY",
            operator="NOT IN",
            required_value="Orthopedics",
        )
        capable = bed(specialties=frozenset({"Cardiology"}))
        self.assertTrue(evaluate(request(requirements=(specialty,)), capable).eligible)

    def test_numeric_operator(self):
        monitoring = requirement(
            requirement_type="MONITORING", operator=">=", required_value="3"
        )
        sufficient = bed(capabilities={"MONITORING": frozenset({"2", "4"})})
        insufficient = bed(capabilities={"MONITORING": frozenset({"1", "2"})})
        self.assertTrue(evaluate(request(requirements=(monitoring,)), sufficient).eligible)
        self.assertFalse(
            evaluate(request(requirements=(monitoring,)), insufficient).eligible
        )

    def test_accessibility_requirement(self):
        accessibility = requirement(requirement_type="ACCESSIBILITY")
        capable = bed(capabilities={"ACCESSIBILITY": frozenset({"yes"})})
        incapable = bed(capabilities={"ACCESSIBILITY": frozenset({"no"})})
        self.assertTrue(evaluate(request(requirements=(accessibility,)), capable).eligible)
        self.assertFalse(
            evaluate(request(requirements=(accessibility,)), incapable).eligible
        )

    def test_room_sharing_and_staffing(self):
        self.assertIn(
            "ROOM_SHARING_CONFLICT",
            codes(evaluate(request(), bed(room_sharing_ok=False))),
        )
        self.assertIn(
            "STAFFED_CAPACITY_EXHAUSTED",
            codes(evaluate(request(), bed(staffed_capacity_available=False))),
        )
        self.assertTrue(evaluate(request(), bed(staffed_capacity_available=None)).eligible)

    def test_requirement_values_are_case_insensitive(self):
        needed = request(
            requirements=(requirement(requirement_type="oxygen", required_value="YES"),)
        )
        capable = bed(capabilities={"OXYGEN": frozenset({"Yes"})})
        self.assertTrue(evaluate(needed, capable).eligible)

    def test_as_dict_shape(self):
        result = evaluate(
            request(requirements=(requirement(),)), bed(state="MAINTENANCE")
        ).as_dict()
        self.assertFalse(result["eligible"])
        self.assertTrue(result["reasons"])
        self.assertIn("code", result["reasons"][0])
        self.assertIn("message", result["reasons"][0])


if __name__ == "__main__":
    unittest.main()
