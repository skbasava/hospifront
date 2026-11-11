# Operational Bed Allocation System — Implementation Plan

**Status:** Phase 0 approved; Phases 1–7 implemented. Installed and validated
on the local Bench site `hospital.localhost` (app version `0.1.0`, commit
`ccb2f6e` plus the Phase 4–7 working tree). The full Bench suite passes 150/150,
including API contracts, the permission matrix and performance benchmarks.
Remote installation on `frappe.abhidigitech.com` has not been performed; it
requires Bench/server access and approval (the API token cannot install apps).
**Sites:** local `hospital.localhost`; remote `https://frappe.abhidigitech.com`
(read-only discovery only)
**Companion analysis:** `docs/01-existing-frappe-schema-analysis.md`
**Raw schema snapshot:** `artifacts/frappe_schema_report.json`

---

## 1. Verified Environment (re-checked)

| Item | Value |
|---|---|
| Authentication | HTTP 200, user `Administrator` (superuser token) |
| Frappe | 15.126.0 (version-15) |
| ERPNext | 15.124.5 (version-15) |
| HRMS | 15.64.8 (version-15) |
| Marley Health (healthcare) | 15.2.5 (version-15) |
| CRM | 15.0.1 |
| DocTypes visible | 1,085 (106 Healthcare, 111 HR) |
| Data state | Fresh: 1 group service unit, 0 patients/admissions/employees, company `test` |

Credentials read only from `FRAPPE_BASE_URL`, `FRAPPE_API_KEY`,
`FRAPPE_API_SECRET`; never logged or written.

---

## 2. Discovered Architecture (existing)

- **No native bed/ward/room DocType exists** (keyword scan: bed/ward/room/
  housekeeping/reservation all absent).
- Marley models physical inpatient space as a **tree of
  `Healthcare Service Unit`** with `occupancy_status` (`Vacant`/`Occupied`).
- **Occupancy source of truth = `Inpatient Occupancy` child table** on
  `Inpatient Record` (`service_unit`, `check_in`, `check_out`, `left`, `invoiced`).
- Admission lifecycle is `Inpatient Record.status`
  (`Admission Scheduled → Admitted → Discharge Scheduled → Discharged | Cancelled`),
  with transfers performed by rolling occupancy rows.
- Ward category/tariff anchor = `Healthcare Service Unit Type`
  (`inpatient_occupancy`, `rate`, `item`).
- HRMS provides raw staffing signals (`Employee`, `Shift Assignment`,
  `Attendance`, `Leave Application`, `Employee Checkin`, `Staffing Plan`) but
  **no employee→ward/competency mapping** and **no clinical staffed-capacity model**.
- Only `Healthcare Administrator`, `Physician`, `Nursing User` roles exist; none
  of the five operational roles are present.

---

## 3. Target Architecture

One Frappe app `hospital_operations` (Python + MariaDB + Frappe workers + Redis),
no extra services. Reuses Marley/ERPNext/HRMS via the ORM.

```
future frontend
      │ Frappe REST (existing auth + roles)
hospital_operations
  api/            beds, requests, allocation, housekeeping, staffing, health
  bed_allocation/ eligibility, priority, cost_model, optimizer (OR-Tools min-cost flow),
                  reservation, transaction, staffing, staffing_service, housekeeping
  integrations/   marley, erpnext, hrms
  tasks.py        expiry sweeper, reconciliation, staffing snapshot refresh,
                  housekeeping task reconciliation
  hospital_operations/doctype/   custom DocTypes
  fixtures/       roles, custom fields (migration-safe)
tools/            remote_client.py, inspect_remote.py (done)
tests/            eligibility, optimizer, reservation, concurrency, staffing, api
docs/             01..08
```

**Reuse rules**
- Occupancy: derive from `Inpatient Occupancy`; never duplicate.
- Operational readiness: additive layer keyed to `Healthcare Service Unit`.
- Admission/transfer/discharge: wrap Marley; never reimplement.
- Billing: ERPNext `Sales Invoice`/`Item` only; never inside the optimiser.

---

## 4. Proposed Schema (new DocTypes)

| DocType | Key fields | Notes / constraints |
|---|---|---|
| `Bed Allocation Request` | patient, inpatient_record, source, priority, care_level, requested_at, requested_by, status, policy_version, clinical_approval_reference, idempotency_key | Status: PENDING…CANCELLED. Unique `idempotency_key`. Index `(status, priority, requested_at)`. |
| `Bed Clinical Requirement` (child) | requirement_type, operator, required_value, mandatory, clinical_reason | Child of request. |
| `Bed Capability` | healthcare_service_unit, capability_type, capability_value, enabled, valid_from, valid_until | One row per capability/bed. Index `(healthcare_service_unit, capability_type)`. |
| `Bed Operational State` | healthcare_service_unit (unique), state, ready_at, version, last_changed_by, last_changed_at | **Unique** on service unit; optimistic `version` column. READY/RESERVED/OCCUPIED/CLEANING_REQUIRED/CLEANING_IN_PROGRESS/ISOLATION_CLEANING/MAINTENANCE/BLOCKED. |
| `Bed Reservation` | allocation_request, healthcare_service_unit, reservation_type, start_at, expires_at, status, created_by, confirmed_at, idempotency_key | Unique `idempotency_key`. **DB constraint: one ACTIVE immediate claim per bed.** Time-window overlap guard for future reservations (SELECT … FOR UPDATE). |
| `Bed Staffing Snapshot` | ward, shift, calculated_at, available_nurses, qualified_nurses, current_patients, staffed_bed_capacity, policy_version | Derived; never treated as presence from schedule alone. |
| `Bed Allocation Policy` | policy_name, version, enabled, clinical_priority_rules, eligibility_rules, cost_weights, waiting_time_rules, reservation_expiry, manual_override_permissions | Versioned; no clinical values hardcoded in Python. |
| `Bed Allocation Run` | run_id, algorithm, status, started_at, completed_at, policy_version, input_hash, request_count, eligible_bed_count, assigned_count, unassigned_count, execution_duration_ms | Deterministic; `input_hash` for reproducibility. |
| `Bed Allocation Proposal` | allocation_run, allocation_request, proposed_bed, cost, priority, eligibility_summary, explanation, status | Explainable; stale-rejection support. |
| `Bed Allocation Event` | event_type, patient_reference, inpatient_record, allocation_request, bed, previous_state, new_state, actor, timestamp, reason, correlation_id | Append-only; no unnecessary clinical detail. |
| `Housekeeping Task` | healthcare_service_unit, task_type, status, assigned_employee, started_at, completed_at, verified_by | Gates VACATED→READY. |
| `Ward Nurse Assignment` (HRMS bridge) | employee, ward/service_unit, competency, valid_from, valid_to | Supplies the missing employee↔ward/competency link. |

**Custom fields (fixtures, not core edits)** — only if strictly needed:
`Healthcare Service Unit`: `ho_ward`, `ho_is_allocatable`, `ho_care_level`.

**Indexes/uniqueness**: as noted per table; occupancy lookup index on
`Inpatient Occupancy(service_unit, left)` via custom index in app install patch
(migration-safe, additive).

---

## 5. Concurrency & Transaction Design (summary)

- Claim flow in one `frappe.db` transaction: `SELECT … FOR UPDATE` on
  `Bed Operational State` (and reservation), re-check occupancy + eligibility +
  staffing, create reservation, call Marley admission/transfer, write audit
  event, commit; rollback on any failure.
- DB-level guarantee: unique active-immediate-claim constraint per service unit.
- Idempotency keys on request + reservation + confirm.
- Optimistic `version` check rejects stale proposals.
- Background jobs: expired-reservation cleanup, reservation↔occupancy
  reconciliation, staffing snapshot refresh and housekeeping task
  reconciliation.
- Marley boundary documented with recoverable reconciliation if its workflow
  cannot join the same transaction.

---

## 6. API Surface (whitelisted methods)

`hospital_operations.api.{beds,requests,allocation,housekeeping,staffing,
post_operative,health}` covering list/availability, request create/list,
optimize/result, confirm/cancel/transfer/discharge, housekeeping
start/complete/verify/cancel, staffing capacity, post-op hold lifecycle and the
health probe. Token auth, role checks, explicit GET/POST decorators,
pagination, idempotency, consistent errors, no guest access, no
`ignore_permissions` shortcuts. Contracts and the permission matrix are in
`docs/05-api-contracts.md`.

**Roles (implemented):** Bed Manager, Admission Coordinator, Nursing
Supervisor, Housekeeping Staff, Hospital Operations Administrator.

---

## 7. Phased Plan (with gates)

| Phase | Scope | Exit criteria / tests |
|---|---|---|
| **0 — Discovery (done)** | Repo + remote read-only schema analysis, mapping, risks | This plan + `01-existing-frappe-schema-analysis.md` approved |
| **1 — Foundation (done)** | App, 12 DocTypes, 5 roles, additive HSU fields, policy+audit scaffolding, indexes/constraint | Installed on `hospital.localhost`; `validate_installation` `ok: true`; 4 DocType + 2 role Bench tests pass |
| **2 — State & Eligibility (done)** | State machine, operational readiness service, capabilities, Stage 1 eligibility engine | 22 eligibility + 7 state tests pass; module installed and health probe OK |
| **3 — Allocation Engine (done)** | OR-Tools min-cost flow, cost/priority model, explainable proposals, run persistence | 10 optimiser + 9 cost/priority tests pass; OR-Tools 9.15.6755 in Bench env |
| **4 — Reservation & ADT (done)** | Transaction-safe claims, Marley admission/transfer/discharge | 11 reservation + 2 concurrency tests; full Bench suite 67/67; ruff/compile pass |
| **5 — HRMS & Housekeeping (done)** | Live staffed capacity from shifts/attendance/leave/competency, derived snapshots, housekeeping lifecycle and READY gate | 17 pure staffing + 10 HRMS integration + 11 housekeeping tests; full Bench suite 105/105; ruff/compile pass |
| **6 — OT & ERPNext (done)** | Post-op time-window holds, delay/cancel/preemption/revalidation, read-only ERPNext billing and discharge reconciliation | 7 pure window + 12 post-op + 6 billing integration tests; full Bench suite 130/130; ruff/compile pass |
| **7 — API & Validation (done)** | Final APIs, permission tests, benchmarks, deployment docs | 11 API contract + 9 permission tests; p50/p95/p99 benchmarks recorded; full Bench suite 150/150; ruff/compile pass |

Deployment, migrate and production data changes require explicit approval at
each gate. No automatic deployment.

---

## 8. Reuse vs Build Summary

**Reuse:** `Patient`, `Patient Encounter`, `Inpatient Record`,
`Inpatient Occupancy`, `Healthcare Service Unit`,
`Healthcare Service Unit Type`, `Medical Department`, `Patient Care Type`,
`Employee`, `Shift Assignment`, `Attendance`, `Leave Application`,
`Sales Invoice`, `Item`.

**Build:** capability registry, operational state, eligibility engine,
priority/cost model, OR-Tools optimiser, transaction-safe reservation,
housekeeping workflow, staffed-capacity snapshots, audit events, policy engine,
API layer, roles, HRMS competency/ward bridge.

---

## 9. Key Risks

1. Dual occupancy/state truth → enforce Marley occupancy as authority; state is additive.
2. Marley admission not submittable → transaction boundary + reconciliation.
3. Superuser token masks least-privilege behavior → re-test with service user.
4. No seed wards/beds/employees → needs approved seeding before functional tests.
5. Missing employee↔ward/competency mapping → add bridge DocType.
6. Unverified Marley server-side validation → inspect source on bench or sandbox-validate.

---

## 10. Decisions Requested Before Phase 1

1. Approve this architecture and the new DocType schema.
2. Confirm the site is a **development/test** instance, not production.
3. Approve seeding 60 synthetic beds (9 General / 12 ICU / 39 Special) and
   ward categories for development/testing.
4. Approve creation of the custom app + DocTypes + roles (no migrations run
   automatically; `bench migrate` will be requested explicitly).
5. Confirm whether a least-privilege service user token can be provided for
   permission testing.
