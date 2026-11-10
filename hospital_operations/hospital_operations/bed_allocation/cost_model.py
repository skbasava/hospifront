"""Assignment cost model (soft preferences only).

Hard clinical constraints live in ``eligibility.py``. Costs here may rank
eligible pairs but can never make an ineligible pair assignable.
"""

import json
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from hospital_operations.bed_allocation.priority import priority_rank

MAX_BASE_COST = 1_000_000
MAX_WAIT_MINUTES = 10_080


@dataclass(frozen=True)
class CostWeights:
    waiting_minutes: int = 1
    care_level_mismatch: int = 50
    specialty_mismatch: int = 100
    transfer_disruption: int = 20
    higher_care_level_use: int = 30
    urgency: int = 0

    @classmethod
    def from_mapping(cls, data):
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise ValueError("cost_weights must be an object")
        defaults = cls()
        values = {field.name: getattr(defaults, field.name) for field in fields(cls)}
        for key, value in data.items():
            if key not in values:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"cost weight {key} must be an integer") from exc
            if number < 0:
                raise ValueError(f"cost weight {key} cannot be negative")
            values[key] = number
        return cls(**values)

    @classmethod
    def from_policy(cls, policy):
        raw = policy.get("cost_weights") if hasattr(policy, "get") else None
        if not raw:
            return cls()
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("cost_weights is not valid JSON") from exc
        return cls.from_mapping(raw)


@dataclass(frozen=True)
class CostBreakdown:
    total: int
    components: dict


def compute_cost(request, bed, weights=None, now=None, priority_order=None):
    weights = weights or CostWeights()
    evaluation_time = _coerce_datetime(now) or datetime.now(timezone.utc)
    components = {}

    waiting = min(_waiting_minutes(request.requested_at, evaluation_time), MAX_WAIT_MINUTES)
    # Longer waits are cheaper to assign, so waiting time improves priority.
    components["waiting"] = (MAX_WAIT_MINUTES - waiting) * weights.waiting_minutes

    if request.care_level != bed.care_level:
        components["care_level"] = weights.care_level_mismatch
        if request.care_level == "Other" and bed.care_level in ("ICU", "Special"):
            components["higher_care_level_use"] = weights.higher_care_level_use
    else:
        components["care_level"] = 0
        components["higher_care_level_use"] = 0

    if request.medical_department and not bed.specialties:
        components["specialty"] = weights.specialty_mismatch
    else:
        components["specialty"] = 0

    components["transfer"] = (
        weights.transfer_disruption if request.source == "Transfer" else 0
    )
    components["urgency"] = weights.urgency * priority_rank(
        request.priority, priority_order
    )

    total = min(sum(components.values()), MAX_BASE_COST)
    return CostBreakdown(total=total, components=components)


def _waiting_minutes(requested_at, evaluation_time):
    if not requested_at:
        return 0
    requested = _coerce_datetime(requested_at)
    if not requested:
        return 0
    seconds = (evaluation_time - requested).total_seconds()
    if seconds <= 0:
        return 0
    return int(seconds // 60)


def _coerce_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
