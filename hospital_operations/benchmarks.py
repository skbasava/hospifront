"""Local synthetic benchmarks for the required operational workflows.

Run only on a development site:

    bench --site hospital.localhost execute hospital_operations.benchmarks.run

The module never runs on a production-looking site. All synthetic records are
removed in ``finally`` blocks, and no real patient data is read.
"""

import math
import statistics
import threading
import time

import frappe
from frappe.utils import now_datetime

from hospital_operations.api import beds as beds_api
from hospital_operations.bed_allocation import optimizer, repository, reservation, staffing_service
from hospital_operations.bed_allocation.eligibility import evaluate
from hospital_operations.tests import factories


def run(iterations=12, concurrent_rounds=5):
    """Measure p50/p95/p99 latency for the required operations."""

    _assert_safe_site()
    iterations = max(3, min(int(iterations), 100))
    concurrent_rounds = max(2, min(int(concurrent_rounds), 20))

    company = factories.get_company()
    unit_type = factories.make_service_unit_type("ICU")
    ward = factories.make_ward(company)
    policy = factories.make_policy(
        staffing={
            "ratio": {"ICU": 100},
            "competency": {"ICU": "ICU"},
            "minimum_nurses": {"ICU": 1},
        }
    )
    previous_policies = _activate_policy(policy)

    created = {
        "beds": [],
        "requests": [],
        "inpatients": [],
        "patients": [],
        "runs": [],
        "proposals": [],
        "employees": [],
        "shift_types": [],
        "shift_assignments": [],
        "attendances": [],
        "nurse_assignments": [],
    }
    try:
        nurse = factories.make_employee(company)
        created["employees"].append(nurse.name)
        shift = factories.make_shift_type("00:01:00", "23:58:00")
        created["shift_types"].append(shift.name)
        created["shift_assignments"].append(
            factories.make_shift_assignment(nurse, shift).name
        )
        created["attendances"].append(factories.make_attendance(nurse, "Present").name)
        created["nurse_assignments"].append(
            factories.make_ward_nurse_assignment(nurse, ward, "ICU").name
        )
        beds = [
            factories.make_bed(unit_type, "ICU", ward, company)
            for _ in range(8)
        ]
        created["beds"].extend(bed.name for bed in beds)
        patient = factories.make_patient()
        inpatient = factories.make_inpatient_record(patient, unit_type, company)
        request = factories.make_request(patient, inpatient, company=company)
        created["patients"].append(patient.name)
        created["inpatients"].append(inpatient.name)
        created["requests"].append(request.name)
        bed_names = [bed.name for bed in beds]

        request_context = repository.get_request_context(request.name)
        contexts = repository.get_bed_contexts(bed_names)
        bed_contexts = [contexts[name] for name in sorted(contexts)]
        eligibility_results = [
            evaluate(request_context, context) for context in bed_contexts
        ]

        results = {}
        results["bed_availability"] = _measure(
            lambda: beds_api.availability(ward=ward.name),
            iterations,
            warmups=2,
        )
        results["clinical_eligibility"] = _measure(
            lambda: repository.evaluate_request(request.name, bed_names),
            iterations,
            warmups=2,
        )
        results["allocation_optimisation"] = _measure(
            lambda: optimizer.optimize(
                [request_context],
                bed_contexts,
                eligibility_results,
                policy_version=policy.version,
                now=now_datetime(),
            ),
            iterations,
            warmups=2,
        )
        results["staffed_capacity"] = _measure(
            lambda: staffing_service.ward_capacity(ward.name),
            iterations,
            warmups=2,
        )

        confirmation_samples = []
        for _ in range(iterations):
            bed = factories.make_bed(unit_type, "ICU", ward, company)
            created["beds"].append(bed.name)
            patient = factories.make_patient()
            inpatient = factories.make_inpatient_record(patient, unit_type, company)
            request = factories.make_request(patient, inpatient, company=company)
            created["patients"].append(patient.name)
            created["inpatients"].append(inpatient.name)
            created["requests"].append(request.name)
            allocation_run, proposal = factories.make_allocation(bed, request, policy)
            created["runs"].append(allocation_run.name)
            created["proposals"].append(proposal.name)
            start = time.perf_counter()
            reservation.confirm(proposal.name, idempotency_key=f"bench-confirm:{proposal.name}")
            confirmation_samples.append((time.perf_counter() - start) * 1000)
        results["reservation_confirmation"] = _summary(confirmation_samples)

        results["concurrent_reservation_attempts"] = _concurrent_benchmark(
            unit_type, ward, company, policy, created, concurrent_rounds
        )
        return {
            "site": frappe.local.site,
            "iterations": iterations,
            "concurrent_rounds": concurrent_rounds,
            "results": results,
        }
    finally:
        try:
            _cleanup(created, ward, unit_type, policy)
        finally:
            _restore_policies(previous_policies)


def _measure(func, iterations, warmups=0):
    for _ in range(warmups):
        func()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        samples.append((time.perf_counter() - start) * 1000)
    return _summary(samples)


def _summary(samples):
    values = sorted(samples)
    return {
        "count": len(values),
        "min_ms": round(values[0], 3),
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "p99_ms": round(_percentile(values, 0.99), 3),
        "max_ms": round(values[-1], 3),
        "mean_ms": round(statistics.fmean(values), 3),
    }


def _percentile(sorted_values, fraction):
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _concurrent_benchmark(unit_type, ward, company, policy, created, rounds):
    samples = []
    wins = 0
    site = frappe.local.site
    sites_path = frappe.local.sites_path
    for _ in range(rounds):
        bed = factories.make_bed(unit_type, "ICU", ward, company)
        created["beds"].append(bed.name)
        patient_a = factories.make_patient()
        inpatient_a = factories.make_inpatient_record(patient_a, unit_type, company)
        request_a = factories.make_request(patient_a, inpatient_a, company=company)
        allocation_run, proposal_a = factories.make_allocation(bed, request_a, policy)
        patient_b = factories.make_patient()
        inpatient_b = factories.make_inpatient_record(patient_b, unit_type, company)
        request_b = factories.make_request(patient_b, inpatient_b, company=company)
        request_b.status = "PROPOSED"
        request_b.save(ignore_permissions=True)
        proposal_b = factories.make_proposal_for_bed(bed, request_b, allocation_run)
        created["patients"].extend([patient_a.name, patient_b.name])
        created["inpatients"].extend([inpatient_a.name, inpatient_b.name])
        created["requests"].extend([request_a.name, request_b.name])
        created["runs"].append(allocation_run.name)
        created["proposals"].extend([proposal_a.name, proposal_b.name])
        frappe.db.commit()

        barrier = threading.Barrier(2)
        results = []
        threads = [
            threading.Thread(
                target=_confirm_in_thread,
                args=(
                    site,
                    sites_path,
                    proposal_a.name,
                    f"bench-a:{proposal_a.name}",
                    barrier,
                    results,
                ),
            ),
            threading.Thread(
                target=_confirm_in_thread,
                args=(
                    site,
                    sites_path,
                    proposal_b.name,
                    f"bench-b:{proposal_b.name}",
                    barrier,
                    results,
                ),
            ),
        ]
        started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        samples.append((time.perf_counter() - started) * 1000)
        wins += sum(1 for kind, _ in results if kind == "success")
        frappe.db.commit()
    summary = _summary(samples)
    summary["rounds"] = rounds
    summary["wins"] = wins
    return summary


def _confirm_in_thread(site, sites_path, proposal, key, barrier, results):
    frappe.init(site=site, sites_path=sites_path)
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        barrier.wait(timeout=15)
        try:
            reservation.confirm(proposal, idempotency_key=key)
            frappe.db.commit()
            results.append(("success", None))
        except Exception as exc:
            frappe.db.rollback()
            results.append(("error", exc))
    finally:
        frappe.destroy()


def _activate_policy(policy):
    previous = frappe.get_all(
        "Bed Allocation Policy",
        filters={"enabled": 1, "name": ("!=", policy.name)},
        pluck="name",
        limit_page_length=0,
    )
    for name in previous:
        frappe.db.set_value("Bed Allocation Policy", name, "enabled", 0)
    frappe.db.set_value("Bed Allocation Policy", policy.name, "enabled", 1)
    if not frappe.get_all(
        "Bed Allocation Policy",
        filters={"enabled": 1},
        pluck="name",
        limit_page_length=0,
    ) == [policy.name]:
        frappe.throw("Could not activate the benchmark allocation policy.")
    return previous


def _restore_policies(names):
    for name in names or ():
        if frappe.db.exists("Bed Allocation Policy", name):
            frappe.db.set_value("Bed Allocation Policy", name, "enabled", 1)
    frappe.db.commit()


def _assert_safe_site():
    site = (frappe.local.site or "").lower()
    if any(token in site for token in ("localhost", "test", "dev")):
        return
    if frappe.conf.get("developer_mode"):
        return
    frappe.throw(
        "Benchmarks are restricted to development sites.", frappe.PermissionError
    )


def _cleanup(created, ward, unit_type, policy):
    try:
        request_names = tuple(created["requests"]) or ("",)
        bed_names = tuple(created["beds"] + [ward.name]) or ("",)
        inpatient_names = tuple(created["inpatients"]) or ("",)
        patient_names = tuple(created["patients"]) or ("",)
        frappe.db.delete(
            "Bed Allocation Event", {"allocation_request": ("in", request_names)}
        )
        frappe.db.delete("Bed Allocation Event", {"bed": ("in", bed_names)})
        frappe.db.delete("Bed Reservation", {"allocation_request": ("in", request_names)})
        frappe.db.delete(
            "Bed Allocation Proposal", {"allocation_request": ("in", request_names)}
        )
        if created["runs"]:
            frappe.db.delete(
                "Bed Allocation Run", {"name": ("in", tuple(created["runs"]))}
            )
        frappe.db.delete(
            "Bed Operational State", {"healthcare_service_unit": ("in", bed_names)}
        )
        frappe.db.delete("Inpatient Occupancy", {"parent": ("in", inpatient_names)})
        if created["attendances"]:
            frappe.db.delete(
                "Attendance", {"name": ("in", tuple(created["attendances"]))}
            )
        if created["shift_assignments"]:
            frappe.db.delete(
                "Shift Assignment",
                {"name": ("in", tuple(created["shift_assignments"]))},
            )
        if created["nurse_assignments"]:
            frappe.db.delete(
                "Ward Nurse Assignment",
                {"name": ("in", tuple(created["nurse_assignments"]))},
            )
        frappe.db.delete("Bed Allocation Request", {"name": ("in", request_names)})
        for name in inpatient_names:
            if name and frappe.db.exists("Inpatient Record", name):
                frappe.delete_doc(
                    "Inpatient Record", name, force=True, ignore_permissions=True
                )
        for name in patient_names:
            if name and frappe.db.exists("Patient", name):
                frappe.delete_doc("Patient", name, force=True, ignore_permissions=True)
        if created["employees"]:
            frappe.db.delete("Employee", {"name": ("in", tuple(created["employees"]))})
        if created["shift_types"]:
            frappe.db.delete(
                "Shift Type", {"name": ("in", tuple(created["shift_types"]))}
            )
        for name in bed_names:
            if name and frappe.db.exists("Healthcare Service Unit", name):
                frappe.delete_doc(
                    "Healthcare Service Unit", name, force=True, ignore_permissions=True
                )
        if frappe.db.exists("Healthcare Service Unit Type", unit_type.name):
            frappe.delete_doc(
                "Healthcare Service Unit Type",
                unit_type.name,
                force=True,
                ignore_permissions=True,
            )
        if frappe.db.exists("Bed Allocation Policy", policy.name):
            frappe.delete_doc(
                "Bed Allocation Policy", policy.name, force=True, ignore_permissions=True
            )
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
