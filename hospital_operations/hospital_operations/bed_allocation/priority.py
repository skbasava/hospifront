"""Priority ordering for the allocation objective.

The order is hospital policy, not a Python constant: it is read from
``Bed Allocation Policy.clinical_priority_rules``. The tuple below is only the
fallback ordering used for tests and for installations that have not configured
a policy yet.
"""

import json

DEFAULT_PRIORITY_ORDER = ("Emergency", "Urgent", "Routine")
KNOWN_PRIORITIES = frozenset(DEFAULT_PRIORITY_ORDER)


class PriorityPolicyError(ValueError):
    pass


def normalise_priority_order(order=None):
    if not order:
        return DEFAULT_PRIORITY_ORDER
    if isinstance(order, str):
        order = [part.strip() for part in order.split(",") if part.strip()]
    order = tuple(order)
    if len(order) != len(set(order)):
        raise PriorityPolicyError("Priority order contains duplicates")
    unknown = set(order) - KNOWN_PRIORITIES
    if unknown:
        raise PriorityPolicyError(f"Unknown priorities: {sorted(unknown)}")
    missing = KNOWN_PRIORITIES - set(order)
    if missing:
        raise PriorityPolicyError(f"Missing priorities: {sorted(missing)}")
    return order


def priority_rank(priority, order=None):
    order = normalise_priority_order(order)
    if priority not in order:
        raise PriorityPolicyError(f"Unknown priority: {priority}")
    return order.index(priority)


def parse_priority_rules(policy=None):
    if policy is None:
        return DEFAULT_PRIORITY_ORDER
    raw = policy.get("clinical_priority_rules") if hasattr(policy, "get") else None
    if not raw:
        return DEFAULT_PRIORITY_ORDER
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise PriorityPolicyError("clinical_priority_rules is not valid JSON") from exc
    if isinstance(raw, dict):
        order = raw.get("order")
    elif isinstance(raw, (list, tuple)):
        order = raw
    else:
        raise PriorityPolicyError("clinical_priority_rules must be a list or object")
    return normalise_priority_order(order)
