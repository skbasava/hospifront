"""Stage 2 — deterministic min-cost-flow allocation.

Pure Python plus OR-Tools. The optimiser never creates admissions, invoices or
occupancy records; callers persist advisory proposals only.

Priority is lexicographic by construction: unassigned penalties for higher
priorities are large enough to dominate any total assignment cost of lower
priorities. Within a priority tier, cost (waiting time, care-level fit, etc.)
and deterministic tie-breaks decide.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Mapping

from hospital_operations.bed_allocation.cost_model import (
    CostWeights,
    compute_cost,
)
from hospital_operations.bed_allocation.eligibility import (
    BedContext,
    RequestContext,
)
from hospital_operations.bed_allocation.priority import normalise_priority_order

ALGORITHM = "min_cost_flow.v1"
TIE_MAX = 1_000_000
MAX_REQUESTS_PER_RUN = 1_000


class OptimizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    request: RequestContext
    bed: BedContext
    base_cost: int
    components: Mapping[str, int]
    matched_requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class Assignment:
    request: str
    bed: str
    cost: int
    priority: str
    explanation: str


@dataclass(frozen=True)
class Unassigned:
    request: str
    priority: str
    reason: str
    eligible_beds: tuple[str, ...] = ()


@dataclass
class OptimizationResult:
    assignments: list[Assignment] = field(default_factory=list)
    unassigned: list[Unassigned] = field(default_factory=list)
    algorithm: str = ALGORITHM
    policy_version: str | None = None
    input_hash: str = ""
    total_cost: int = 0
    request_count: int = 0
    eligible_bed_count: int = 0

    def as_dict(self):
        return {
            "assignments": [asdict(item) for item in self.assignments],
            "unassigned": [asdict(item) for item in self.unassigned],
            "algorithm": self.algorithm,
            "policy_version": self.policy_version,
            "input_hash": self.input_hash,
            "total_cost": self.total_cost,
            "request_count": self.request_count,
            "eligible_bed_count": self.eligible_bed_count,
        }


def optimize(
    requests,
    beds,
    eligibility_results,
    weights=None,
    priority_order=None,
    policy_version=None,
    now=None,
):
    requests = sorted(requests, key=lambda item: item.name)
    beds = sorted(beds, key=lambda item: item.name)
    if len(requests) > MAX_REQUESTS_PER_RUN:
        raise OptimizationError(
            f"Too many requests for one run ({len(requests)} > "
            f"{MAX_REQUESTS_PER_RUN}); split the batch."
        )
    order = normalise_priority_order(priority_order)
    for request in requests:
        if request.priority not in order:
            raise OptimizationError(f"Unknown priority for {request.name}")
    weights = weights or CostWeights()
    evaluation_time = _evaluation_time(now)

    eligible = {
        (result.request, result.bed): result for result in eligibility_results
    }
    candidates = []
    for request in requests:
        for bed in beds:
            result = eligible.get((request.name, bed.name))
            if not result or not result.eligible:
                continue
            breakdown = compute_cost(
                request, bed, weights, evaluation_time, order
            )
            candidates.append(
                Candidate(
                    request=request,
                    bed=bed,
                    base_cost=breakdown.total,
                    components=dict(breakdown.components),
                    matched_requirements=tuple(result.matched_requirements),
                )
            )

    eligible_bed_count = len({candidate.bed.name for candidate in candidates})
    input_hash = _input_hash(
        requests, beds, eligible.values(), weights, order, evaluation_time
    )

    if not requests:
        return OptimizationResult(
            policy_version=policy_version,
            input_hash=input_hash,
            request_count=0,
            eligible_bed_count=0,
        )

    assignments, unassigned_requests = _solve(
        requests, beds, candidates, order, evaluation_time
    )
    by_name = {request.name: request for request in requests}
    candidate_by_request = {}
    for candidate in candidates:
        candidate_by_request.setdefault(candidate.request.name, []).append(candidate)

    result_assignments = [
        Assignment(
            request=name,
            bed=bed_name,
            cost=base_cost,
            priority=by_name[name].priority,
            explanation=_explain(name, bed_name, candidate_by_request, by_name),
        )
        for name, bed_name, base_cost in assignments
    ]
    result_unassigned = []
    for name in unassigned_requests:
        eligible_beds = tuple(
            sorted(candidate.bed.name for candidate in candidate_by_request.get(name, []))
        )
        reason = (
            "No eligible bed after mandatory clinical constraints"
            if not eligible_beds
            else "Eligible beds were assigned to competing requests"
        )
        result_unassigned.append(
            Unassigned(
                request=name,
                priority=by_name[name].priority,
                reason=reason,
                eligible_beds=eligible_beds,
            )
        )

    return OptimizationResult(
        assignments=sorted(result_assignments, key=lambda item: item.request),
        unassigned=sorted(result_unassigned, key=lambda item: item.request),
        policy_version=policy_version,
        input_hash=input_hash,
        total_cost=sum(item.cost for item in result_assignments),
        request_count=len(requests),
        eligible_bed_count=eligible_bed_count,
    )


def _solve(requests, beds, candidates, order, evaluation_time):
    request_count = len(requests)
    bed_count = len(beds)

    # A stable per-pair tie value makes equal-cost solutions deterministic.
    # Base cost always dominates the tie term; the tier penalties below always
    # dominate base cost.
    tie_scale = TIE_MAX * (request_count + 1)
    scaled_candidates = []
    max_assignment_cost = 0
    for candidate in candidates:
        tie = _stable_tie(candidate.request.name, candidate.bed.name)
        scaled_cost = candidate.base_cost * tie_scale + tie
        max_assignment_cost = max(max_assignment_cost, scaled_cost)
        scaled_candidates.append((candidate, scaled_cost))

    step = max(1, (max_assignment_cost + 1) * (request_count + 1) * 2)
    penalties = {
        priority: step * (len(order) - index)
        for index, priority in enumerate(order)
    }

    from ortools.graph.python import min_cost_flow

    solver = min_cost_flow.SimpleMinCostFlow()
    source = 0
    request_node = {request.name: 1 + index for index, request in enumerate(requests)}
    bed_node = {
        bed.name: 1 + request_count + index for index, bed in enumerate(beds)
    }
    sink = 1 + request_count + bed_count

    for request in requests:
        solver.add_arc_with_capacity_and_unit_cost(
            source, request_node[request.name], 1, 0
        )

    candidate_arcs = []
    for candidate, scaled_cost in scaled_candidates:
        arc = solver.add_arc_with_capacity_and_unit_cost(
            request_node[candidate.request.name],
            bed_node[candidate.bed.name],
            1,
            scaled_cost,
        )
        candidate_arcs.append((arc, candidate))

    unassigned_arcs = {}
    for request in requests:
        arc = solver.add_arc_with_capacity_and_unit_cost(
            request_node[request.name],
            sink,
            1,
            penalties[request.priority],
        )
        unassigned_arcs[request.name] = arc

    for bed in beds:
        solver.add_arc_with_capacity_and_unit_cost(bed_node[bed.name], sink, 1, 0)

    solver.set_node_supply(source, request_count)
    solver.set_node_supply(sink, -request_count)

    status = solver.solve()
    optimal = getattr(solver, "OPTIMAL", 0)
    if status != optimal:
        raise OptimizationError(
            f"Min-cost flow did not solve optimally (status {status})"
        )

    assignments = []
    assigned_requests = set()
    for arc, candidate in candidate_arcs:
        if solver.flow(arc) == 1:
            assignments.append(
                (
                    candidate.request.name,
                    candidate.bed.name,
                    candidate.base_cost,
                )
            )
            assigned_requests.add(candidate.request.name)

    unassigned = [
        request.name
        for request in requests
        if request.name not in assigned_requests
        and solver.flow(unassigned_arcs[request.name]) == 1
    ]
    return assignments, unassigned


def _explain(request_name, bed_name, candidate_by_request, requests):
    for candidate in candidate_by_request.get(request_name, []):
        if candidate.bed.name != bed_name:
            continue
        components = ", ".join(
            f"{key}={value}" for key, value in sorted(candidate.components.items())
        )
        matched = ", ".join(candidate.matched_requirements) or "none"
        return (
            f"priority={requests[request_name].priority}; bed={bed_name}; "
            f"components: {components}; matched requirements: {matched}"
        )
    return f"bed={bed_name}"


def _input_hash(requests, beds, eligibility, weights, order, evaluation_time):
    payload = {
        "requests": requests,
        "beds": beds,
        "eligibility": [result.as_dict() for result in eligibility],
        "weights": weights,
        "priority_order": order,
        "evaluation_time": evaluation_time,
    }
    encoded = json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical(value):
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _stable_tie(request_name, bed_name):
    digest = hashlib.sha256(
        f"{request_name}\x1f{bed_name}".encode("utf-8")
    ).hexdigest()
    return int(digest[:12], 16) % TIE_MAX


def _evaluation_time(now):
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
