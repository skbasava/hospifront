import unittest
from datetime import datetime, timedelta, timezone

from hospital_operations.bed_allocation.cost_model import (
    MAX_WAIT_MINUTES,
    CostWeights,
    compute_cost,
)
from hospital_operations.bed_allocation.eligibility import BedContext, RequestContext
from hospital_operations.bed_allocation.priority import (
    PriorityPolicyError,
    normalise_priority_order,
    parse_priority_rules,
    priority_rank,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def request(**overrides):
    values = {
        "name": "BAR-0001",
        "care_level": "ICU",
        "priority": "Routine",
        "requested_at": None,
        "source": "Direct Admission",
    }
    values.update(overrides)
    return RequestContext(**values)


def bed(**overrides):
    values = {
        "name": "ICU-BED-01",
        "care_level": "ICU",
        "state": "READY",
        "specialties": frozenset(),
    }
    values.update(overrides)
    return BedContext(**values)


def zero_weights(**overrides):
    values = {
        "waiting_minutes": 0,
        "care_level_mismatch": 0,
        "specialty_mismatch": 0,
        "transfer_disruption": 0,
        "higher_care_level_use": 0,
        "urgency": 0,
    }
    values.update(overrides)
    return CostWeights(**values)


class TestCostModel(unittest.TestCase):
    def test_no_wait_fills_waiting_component(self):
        breakdown = compute_cost(request(), bed(), zero_weights(waiting_minutes=1), NOW)
        self.assertEqual(breakdown.components["waiting"], MAX_WAIT_MINUTES)
        self.assertEqual(breakdown.total, MAX_WAIT_MINUTES)

    def test_longer_wait_is_cheaper(self):
        short = request(requested_at=NOW - timedelta(minutes=10))
        long = request(requested_at=NOW - timedelta(minutes=100))
        weights = zero_weights(waiting_minutes=1)
        short_cost = compute_cost(short, bed(), weights, NOW)
        long_cost = compute_cost(long, bed(), weights, NOW)
        self.assertLess(long_cost.total, short_cost.total)

    def test_future_request_has_no_wait_credit(self):
        future = request(requested_at=NOW + timedelta(minutes=30))
        breakdown = compute_cost(future, bed(), zero_weights(waiting_minutes=1), NOW)
        self.assertEqual(breakdown.components["waiting"], MAX_WAIT_MINUTES)

    def test_care_level_mismatch_components(self):
        breakdown = compute_cost(
            request(care_level="Other"),
            bed(care_level="ICU"),
            zero_weights(care_level_mismatch=10, higher_care_level_use=5),
            NOW,
        )
        self.assertEqual(breakdown.components["care_level"], 10)
        self.assertEqual(breakdown.components["higher_care_level_use"], 5)
        self.assertEqual(breakdown.total, 15)

    def test_specialty_missing_is_soft_penalty(self):
        breakdown = compute_cost(
            request(medical_department="Cardiology"),
            bed(specialties=frozenset()),
            zero_weights(specialty_mismatch=7),
            NOW,
        )
        self.assertEqual(breakdown.components["specialty"], 7)

    def test_weights_reject_negative_and_non_integer(self):
        with self.assertRaises(ValueError):
            CostWeights.from_mapping({"waiting_minutes": -1})
        with self.assertRaises(ValueError):
            CostWeights.from_mapping({"waiting_minutes": "soon"})

    def test_weights_from_policy_json(self):
        policy = {"cost_weights": '{"waiting_minutes": 2, "urgency": 3}'}
        weights = CostWeights.from_policy(policy)
        self.assertEqual(weights.waiting_minutes, 2)
        self.assertEqual(weights.urgency, 3)

    def test_priority_rules_parse(self):
        self.assertEqual(
            parse_priority_rules(None), ("Emergency", "Urgent", "Routine")
        )
        self.assertEqual(
            parse_priority_rules({"clinical_priority_rules": '{"order": ["Urgent", "Emergency", "Routine"]}'}),
            ("Urgent", "Emergency", "Routine"),
        )
        self.assertEqual(
            normalise_priority_order(["Emergency", "Urgent", "Routine"]),
            ("Emergency", "Urgent", "Routine"),
        )
        self.assertEqual(priority_rank("Routine"), 2)

    def test_priority_rules_reject_invalid_order(self):
        with self.assertRaises(PriorityPolicyError):
            normalise_priority_order(["Emergency", "Routine"])
        with self.assertRaises(PriorityPolicyError):
            normalise_priority_order(["Emergency", "Urgent", "Routine", "Urgent"])
        with self.assertRaises(PriorityPolicyError):
            normalise_priority_order(["Emergency", "Urgent", "Unknown"])


if __name__ == "__main__":
    unittest.main()
