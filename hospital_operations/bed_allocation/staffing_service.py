"""Operational staffing-capacity service.

Combines the pure staffing rules with read-only HRMS signals and Marley
occupancy, then persists derived ``Bed Staffing Snapshot`` documents. No
clinical ratio is hardcoded: when the active policy has no ``staffing`` object,
capacity checks are disabled and allocation is not blocked.
"""

import json
from collections import Counter

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from hospital_operations.bed_allocation.staffing import (
    StaffingResult,
    StaffingRulesError,
    compute,
    parse_rules,
)
from hospital_operations.integrations import hrms
from hospital_operations.utils.config import get_active_policy


def ward_capacity(ward, at=None, policy=None, rules=None):
    """Compute live capacity for one ward without persisting anything."""

    at = get_datetime(at or now_datetime())
    rules, policy = _rules_and_policy(policy=policy, rules=rules)
    beds = _ward_beds([ward])
    metrics = _ward_metrics([ward], beds)
    if rules is None:
        return _disabled_result(ward, metrics, policy)
    signals = hrms.build_nurse_signals([ward], at, rules)
    return _compute_result(
        ward, rules, policy, metrics, signals.get(ward, [])
    )


def load_ward_capacity_map(wards, at=None, policy=None):
    """Compute live capacity for many wards with batched HRMS queries."""

    wards = sorted({ward for ward in wards if ward})
    if not wards:
        return {}
    at = get_datetime(at or now_datetime())
    rules, policy = _rules_and_policy(policy=policy)
    beds = _ward_beds(wards)
    metrics = _ward_metrics(wards, beds)
    if rules is None:
        return {
            ward: _disabled_result(ward, metrics, policy) for ward in wards
        }
    signals = hrms.build_nurse_signals(wards, at, rules)
    return {
        ward: _compute_result(
            ward, rules, policy, metrics, signals.get(ward, [])
        )
        for ward in wards
    }


def staffing_availability(wards, at=None, policy=None):
    """Return ``ward -> bool | None`` for eligibility evaluation.

    ``None`` means the policy does not configure staffing, so the constraint is
    intentionally not enforced. ``False`` means no staffed bed is available.
    """

    results = load_ward_capacity_map(wards, at=at, policy=policy)
    return {
        ward: (
            None
            if result is None or not result.enabled
            else result.available_capacity > 0
        )
        for ward, result in results.items()
    }


def refresh_ward_snapshot(ward, at=None, policy=None):
    """Persist a derived capacity snapshot for one ward."""

    at = get_datetime(at or now_datetime())
    result = ward_capacity(ward, at=at, policy=policy)
    if not result.enabled:
        return None
    notes = {
        "care_level": result.care_level,
        "patients_per_nurse": result.notes.get("patients_per_nurse"),
        "required_competency": result.notes.get("required_competency"),
        "available_capacity": result.available_capacity,
        "exclusions": result.exclusions,
    }
    if result.notes.get("below_minimum_nurses"):
        notes["below_minimum_nurses"] = result.notes["below_minimum_nurses"]
    return frappe.get_doc(
        {
            "doctype": "Bed Staffing Snapshot",
            "ward": ward,
            "shift": result.shift,
            "calculated_at": at,
            "available_nurses": result.available_nurses,
            "qualified_nurses": result.qualified_nurses,
            "current_patients": result.current_patients,
            "staffed_bed_capacity": result.staffed_bed_capacity,
            "policy_version": result.policy_version,
            "notes": json.dumps(notes, sort_keys=True, default=str),
        }
    ).insert(ignore_permissions=True)


def refresh_all_wards(at=None):
    """Refresh snapshots for every ward with allocatable beds."""

    at = get_datetime(at or now_datetime())
    wards = _allocatable_wards()
    snapshots = []
    failed = []
    for ward in wards:
        try:
            snapshot = refresh_ward_snapshot(ward, at=at)
        except Exception as exc:  # pragma: no cover - defensive scheduler path
            failed.append(ward)
            frappe.log_error(
                title="Staffing snapshot refresh failed",
                message=f"{ward}: {exc}",
            )
            continue
        if snapshot:
            snapshots.append(snapshot.name)
    return {
        "wards": len(wards),
        "snapshots": snapshots,
        "failed": failed,
    }


def latest_snapshot(ward):
    names = frappe.get_all(
        "Bed Staffing Snapshot",
        filters={"ward": ward},
        pluck="name",
        order_by="calculated_at desc, creation desc",
        limit_page_length=1,
    )
    if not names:
        return None
    return frappe.get_doc("Bed Staffing Snapshot", names[0])


def _rules_and_policy(policy=None, rules=None):
    if rules is not None:
        return rules, policy
    policy = policy or get_active_policy()
    if not policy:
        return None, None
    try:
        parsed = parse_rules(policy.eligibility_rules)
    except StaffingRulesError as exc:
        frappe.throw(_("Invalid staffing policy: {0}").format(exc))
    return parsed, policy


def _compute_result(ward, rules, policy, metrics, signals):
    care_level, physical_beds, current_patients = metrics.get(
        ward, ("Other", 0, 0)
    )
    return compute(
        rules,
        ward=ward,
        care_level=care_level,
        nurses=signals,
        current_patients=current_patients,
        physical_beds=physical_beds,
        shift=_dominant_shift(signals),
        policy_version=policy.version if policy else None,
    )


def _disabled_result(ward, metrics, policy):
    care_level, physical_beds, current_patients = metrics.get(
        ward, ("Other", 0, 0)
    )
    return StaffingResult(
        ward=ward,
        care_level=care_level,
        enabled=False,
        current_patients=current_patients,
        physical_beds=physical_beds,
        policy_version=policy.version if policy else None,
        notes={"reason": "no_staffing_policy"},
    )


def _allocatable_wards():
    rows = frappe.get_all(
        "Healthcare Service Unit",
        filters={"ho_is_allocatable": 1, "is_group": 0},
        fields=["ho_ward", "parent_healthcare_service_unit"],
        limit_page_length=0,
    )
    return sorted(
        {
            row.ho_ward or row.parent_healthcare_service_unit
            for row in rows
            if row.ho_ward or row.parent_healthcare_service_unit
        }
    )


def _ward_beds(wards):
    ward_set = set(wards)
    rows = frappe.get_all(
        "Healthcare Service Unit",
        filters={"ho_is_allocatable": 1, "is_group": 0},
        fields=[
            "name",
            "ho_ward",
            "parent_healthcare_service_unit",
            "ho_care_level",
        ],
        limit_page_length=0,
    )
    beds = {}
    for row in rows:
        ward = row.ho_ward or row.parent_healthcare_service_unit
        if ward not in ward_set:
            continue
        beds[row.name] = {
            "ward": ward,
            "care_level": row.ho_care_level or "Other",
        }
    return beds


def _ward_metrics(wards, beds):
    physical = Counter()
    levels = {ward: Counter() for ward in wards}
    bed_to_ward = {}
    for bed, details in beds.items():
        ward = details["ward"]
        bed_to_ward[bed] = ward
        physical[ward] += 1
        levels[ward][details["care_level"]] += 1
    current = _occupancy_counts(bed_to_ward)
    metrics = {}
    for ward in wards:
        counts = levels.get(ward) or Counter()
        care_level = (
            sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            if counts
            else "Other"
        )
        metrics[ward] = (care_level, physical[ward], current.get(ward, 0))
    return metrics


def _occupancy_counts(bed_to_ward):
    if not bed_to_ward:
        return {}
    bed_names = sorted(bed_to_ward)
    placeholders = ", ".join(["%s"] * len(bed_names))
    rows = frappe.db.sql(
        f"""
        SELECT service_unit, COUNT(*) AS total
        FROM `tabInpatient Occupancy`
        WHERE parenttype = 'Inpatient Record'
          AND service_unit IN ({placeholders})
          AND check_out IS NULL
        GROUP BY service_unit
        """,
        tuple(bed_names),
        as_dict=True,
    )
    counts = Counter()
    for row in rows:
        counts[bed_to_ward[row.service_unit]] += int(row.total or 0)
    return counts


def _dominant_shift(signals):
    counts = Counter(
        signal.shift_type
        for signal in signals
        if signal.shift_type and signal.on_shift
    )
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
