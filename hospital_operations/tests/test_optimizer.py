import unittest
from datetime import datetime, timedelta, timezone

try:
    import ortools  # noqa: F401
except ImportError:
    raise unittest.SkipTest("OR-Tools is required for optimiser tests")

from hospital_operations.bed_allocation.cost_model import CostWeights
from hospital_operations.bed_allocation.eligibility import BedContext, RequestContext, evaluate
from hospital_operations.bed_allocation.optimizer import optimize

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
ZERO = CostWeights(
    waiting_minutes=0,
    care_level_mismatch=0,
    specialty_mismatch=0,
    transfer_disruption=0,
    higher_care_level_use=0,
    urgency=0,
)


def request(name, priority="Routine", care_level="ICU", **overrides):
    return RequestContext(
        name=name, care_level=care_level, priority=priority, **overrides
    )


def bed(name, care_level="ICU", state="READY", **overrides):
    return BedContext(name=name, care_level=care_level, state=state, **overrides)


def results(requests, beds):
    return [evaluate(req, bed_ctx) for req in requests for bed_ctx in beds]


def assignment_map(result):
    return {item.request: item.bed for item in result.assignments}


class TestOptimizer(unittest.TestCase):
    def test_emergency_beats_routine_for_one_bed(self):
        requests = [request("A-Routine"), request("B-Emergency", "Emergency")]
        beds = [bed("ICU-1")]
        result = optimize(requests, beds, results(requests, beds), weights=ZERO, now=NOW)
        self.assertEqual(assignment_map(result), {"B-Emergency": "ICU-1"})
        self.assertEqual([item.request for item in result.unassigned], ["A-Routine"])

    def test_priority_order_is_lexicographic(self):
        requests = [
            request("A-Routine"),
            request("B-Urgent", "Urgent"),
            request("C-Emergency", "Emergency"),
        ]
        beds = [bed("ICU-1")]
        result = optimize(requests, beds, results(requests, beds), weights=ZERO, now=NOW)
        self.assertEqual(assignment_map(result), {"C-Emergency": "ICU-1"})

    def test_multiple_requests_compete_for_one_bed(self):
        requests = [request("A"), request("B")]
        beds = [bed("ICU-1")]
        result = optimize(requests, beds, results(requests, beds), weights=ZERO, now=NOW)
        self.assertEqual(len(result.assignments), 1)
        self.assertEqual(len(result.unassigned), 1)
        self.assertIn(
            "competing requests", result.unassigned[0].reason
        )

    def test_all_compatible_beds_are_used(self):
        requests = [request("A"), request("B")]
        beds = [bed("ICU-1"), bed("ICU-2")]
        result = optimize(requests, beds, results(requests, beds), weights=ZERO, now=NOW)
        self.assertEqual(len(result.assignments), 2)
        self.assertEqual(len(result.unassigned), 0)
        self.assertEqual(result.eligible_bed_count, 2)

    def test_no_eligible_bed(self):
        requests = [request("A")]
        beds = [bed("GEN-1", care_level="General")]
        result = optimize(requests, beds, results(requests, beds), weights=ZERO, now=NOW)
        self.assertEqual(result.assignments, [])
        self.assertEqual(
            result.unassigned[0].reason,
            "No eligible bed after mandatory clinical constraints",
        )
        self.assertEqual(result.eligible_bed_count, 0)

    def test_soft_cost_cannot_assign_ineligible_pair(self):
        requests = [request("A")]
        beds = [bed("ICU-1"), bed("GEN-1", care_level="General")]
        result = optimize(requests, beds, results(requests, beds), weights=ZERO, now=NOW)
        self.assertEqual(assignment_map(result), {"A": "ICU-1"})
        self.assertEqual(result.eligible_bed_count, 1)

    def test_deterministic_ties(self):
        requests = [request("A"), request("B")]
        beds = [bed("ICU-1"), bed("ICU-2")]
        first = optimize(
            requests, beds, results(requests, beds), weights=ZERO, now=NOW
        ).as_dict()
        second = optimize(
            list(reversed(requests)),
            list(reversed(beds)),
            results(requests, beds),
            weights=ZERO,
            now=NOW,
        ).as_dict()
        self.assertEqual(first["assignments"], second["assignments"])
        self.assertEqual(first["unassigned"], second["unassigned"])
        self.assertEqual(first["input_hash"], second["input_hash"])

    def test_waiting_time_preference(self):
        requests = [
            request("A", requested_at=NOW - timedelta(minutes=10)),
            request("B", requested_at=NOW - timedelta(minutes=100)),
        ]
        beds = [bed("ICU-1")]
        result = optimize(
            requests,
            beds,
            results(requests, beds),
            weights=CostWeights(
                waiting_minutes=1,
                care_level_mismatch=0,
                specialty_mismatch=0,
                transfer_disruption=0,
                higher_care_level_use=0,
                urgency=0,
            ),
            now=NOW,
        )
        self.assertEqual(assignment_map(result), {"B": "ICU-1"})

    def test_input_hash_changes_with_input(self):
        requests = [request("A")]
        beds = [bed("ICU-1")]
        first = optimize(requests, beds, results(requests, beds), weights=ZERO, now=NOW)
        changed = [request("A", priority="Emergency")]
        second = optimize(
            changed, beds, results(changed, beds), weights=ZERO, now=NOW
        )
        self.assertNotEqual(first.input_hash, second.input_hash)

    def test_result_shape(self):
        requests = [request("A")]
        beds = [bed("ICU-1")]
        data = optimize(
            requests, beds, results(requests, beds), weights=ZERO, now=NOW
        ).as_dict()
        self.assertEqual(data["algorithm"], "min_cost_flow.v1")
        self.assertEqual(data["request_count"], 1)
        self.assertIn("input_hash", data)
        self.assertEqual(len(data["assignments"]), 1)
        self.assertEqual(data["assignments"][0]["request"], "A")


if __name__ == "__main__":
    unittest.main()
