"""Frappe persistence for allocation runs and advisory proposals.

The optimiser is pure; this module loads contexts, persists the run and
proposals, appends audit events, and updates request status. It never creates
admissions, occupancy rows or invoices.
"""

import json
import time

import frappe
from frappe import _
from frappe.utils import now_datetime

from hospital_operations.bed_allocation import repository
from hospital_operations.bed_allocation.cost_model import CostWeights
from hospital_operations.bed_allocation.eligibility import evaluate
from hospital_operations.bed_allocation.optimizer import optimize
from hospital_operations.bed_allocation.priority import parse_priority_rules
from hospital_operations.utils.audit import log_event

ALLOWED_REQUEST_STATUSES = ("PENDING", "ELIGIBILITY_CHECKED")


def create_allocation_run(request_names=None, policy_name=None, bed_names=None):
    policy = _get_policy(policy_name)
    request_names = _normalise_names(request_names)
    if request_names is None:
        request_names = frappe.get_all(
            "Bed Allocation Request",
            filters={"status": ("in", ALLOWED_REQUEST_STATUSES)},
            pluck="name",
            order_by="requested_at asc",
            limit_page_length=0,
        )
    if not request_names:
        frappe.throw(_("There are no pending bed allocation requests."))

    _validate_request_statuses(request_names)
    requests = [repository.get_request_context(name) for name in request_names]

    if isinstance(bed_names, str):
        bed_names = [part.strip() for part in bed_names.split(",") if part.strip()]
    if bed_names is None:
        bed_names = frappe.get_all(
            "Healthcare Service Unit",
            filters={"ho_is_allocatable": 1, "is_group": 0},
            pluck="name",
            limit_page_length=0,
        )
    bed_contexts = repository.get_bed_contexts(bed_names)
    beds = sorted(bed_contexts.values(), key=lambda item: item.name)

    care_level_map = _care_level_map(policy)
    eligibility_results = [
        evaluate(request, bed, care_level_map)
        for request in requests
        for bed in beds
    ]
    eligibility_map = {
        (result.request, result.bed): result for result in eligibility_results
    }

    started_at = now_datetime()
    started = time.monotonic()
    result = optimize(
        requests=requests,
        beds=beds,
        eligibility_results=eligibility_results,
        weights=CostWeights.from_policy(policy),
        priority_order=parse_priority_rules(policy),
        policy_version=policy.version,
        now=started_at,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    run_id = (
        "RUN-"
        + started_at.strftime("%Y%m%d%H%M%S")
        + "-"
        + frappe.generate_hash(length=6)
    )
    run = frappe.get_doc(
        {
            "doctype": "Bed Allocation Run",
            "run_id": run_id,
            "algorithm": result.algorithm,
            "status": "COMPLETED",
            "started_at": started_at,
            "completed_at": now_datetime(),
            "execution_duration_ms": duration_ms,
            "policy": policy.name,
            "policy_version": policy.version,
            "input_hash": result.input_hash,
            "request_count": result.request_count,
            "eligible_bed_count": result.eligible_bed_count,
            "assigned_count": len(result.assignments),
            "unassigned_count": len(result.unassigned),
        }
    ).insert(ignore_permissions=True)

    for assignment in result.assignments:
        eligibility = eligibility_map[(assignment.request, assignment.bed)]
        bed = bed_contexts[assignment.bed]
        proposal = frappe.get_doc(
            {
                "doctype": "Bed Allocation Proposal",
                "allocation_run": run.name,
                "allocation_request": assignment.request,
                "proposed_bed": assignment.bed,
                "cost": assignment.cost,
                "priority": assignment.priority,
                "bed_state_version": bed.state_version or 0,
                "eligibility_summary": json.dumps(
                    eligibility.as_dict(), sort_keys=True, separators=(",", ":")
                ),
                "explanation": assignment.explanation,
                "status": "PROPOSED",
            }
        ).insert(ignore_permissions=True)
        log_event(
            "PROPOSAL_GENERATED",
            bed=assignment.bed,
            allocation_request=assignment.request,
            correlation_id=run.name,
            reason="Allocation proposal generated",
            details={
                "run": run.name,
                "proposal": proposal.name,
                "cost": assignment.cost,
                "priority": assignment.priority,
            },
        )

    assigned_requests = {item.request for item in result.assignments}
    for request_name in request_names:
        status = "PROPOSED" if request_name in assigned_requests else "WAITLISTED"
        frappe.db.set_value(
            "Bed Allocation Request",
            request_name,
            {"status": status, "policy_version": policy.version},
        )

    return run


def _get_policy(policy_name=None):
    if policy_name:
        policy = frappe.get_doc("Bed Allocation Policy", policy_name)
        if not policy.enabled:
            frappe.throw(_("Bed Allocation Policy {0} is disabled.").format(policy_name))
        return policy
    names = frappe.get_all(
        "Bed Allocation Policy",
        filters={"enabled": 1},
        pluck="name",
        order_by="modified desc",
        limit_page_length=1,
    )
    if not names:
        frappe.throw(
            _("No enabled Bed Allocation Policy exists. Configure one before running.")
        )
    return frappe.get_doc("Bed Allocation Policy", names[0])


def _normalise_names(names):
    if names is None:
        return None
    if isinstance(names, str):
        names = [part.strip() for part in names.split(",") if part.strip()]
    return list(dict.fromkeys(names))


def _validate_request_statuses(request_names):
    rows = frappe.get_all(
        "Bed Allocation Request",
        filters={"name": ("in", request_names)},
        fields=["name", "status"],
        limit_page_length=0,
    )
    found = {row.name: row.status for row in rows}
    missing = [name for name in request_names if name not in found]
    if missing:
        frappe.throw(_("Unknown allocation requests: {0}").format(", ".join(missing)))
    invalid = [
        name
        for name, status in found.items()
        if status not in ALLOWED_REQUEST_STATUSES
    ]
    if invalid:
        frappe.throw(
            _("Requests are not pending: {0}").format(", ".join(sorted(invalid)))
        )


def _care_level_map(policy):
    raw = policy.eligibility_rules
    if not raw:
        return None
    try:
        rules = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        frappe.throw(_("eligibility_rules is not valid JSON."))
    compatibility = rules.get("care_level_compatibility") if isinstance(rules, dict) else None
    if not compatibility:
        return None
    return {
        level: frozenset(values if isinstance(values, (list, tuple, set)) else [values])
        for level, values in compatibility.items()
    }
