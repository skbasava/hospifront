# Final Implementation Report (Phases 0–7)

**Status:** implementation complete on the local Bench site
`hospital.localhost`. Production installation, migration and data seeding remain
approval-gated and have not been performed.

**Date:** 2026-11-11
**App version:** `0.1.0`
**Bench app branch:** `bff_development` (base commit `ccb2f6e` plus the Phase
4–7 working tree)
**Remote site:** `https://frappe.abhidigitech.com` — read-only discovery only,
never modified.

---

## 1. Implemented features

| Area | Delivered capability |
|---|---|
| Bed registry | Operational layer over `Healthcare Service Unit` with ward, care level, allocatable flag, capabilities, specialties and operational state |
| Bed lifecycle | READY, RESERVED, OCCUPIED, VACATED, cleaning states, MAINTENANCE, BLOCKED with explicit transitions and optimistic version checks |
| Clinical eligibility | Hard constraints for care level, specialty, equipment, isolation, readiness, occupancy, reservations, room sharing and staffed capacity |
| Allocation engine | Deterministic OR-Tools min-cost-flow optimisation with priority-preserving objective and explainable proposals |
| Reservations and ADT | Transaction-safe immediate reservations, Marley admission, transfer, discharge, expiry and reconciliation |
| HRMS staffing | Live staffed-capacity evaluation from Employee, Shift Assignment, Shift Type, Attendance, Leave Application and Employee Checkin |
| Housekeeping | Task lifecycle and READY gate; a cleaning bed cannot become READY without a verified task |
| OT / post-operative | Future half-open time-window holds, rescheduling, cancellation, confirmation, expiry and clinically approved emergency pre-emption |
| ERPNext billing | Read-only tariff, occupancy-period, invoice-reference and discharge-reconciliation helpers; no invoice creation in allocation paths |
| APIs | Token-authenticated, role-gated REST endpoints with pagination, idempotency, validation and consistent errors |
| Audit | Append-only Bed Allocation Event history for requests, proposals, reservations, admissions, transfers, discharges, housekeeping and overrides |
| Operations | Scheduler jobs for reservation expiry, reconciliation, staffing snapshots and housekeeping task reconciliation |

---

## 2. Files created or modified

### Application code

- `hospital_operations/hooks.py`, `tasks.py`, `install.py`, `benchmarks.py`
- `hospital_operations/api/`: `common.py`, `beds.py`, `requests.py`,
  `allocation.py`, `housekeeping.py`, `staffing.py`, `post_operative.py`,
  `health.py`
- `hospital_operations/bed_allocation/`: `eligibility.py`, `priority.py`,
  `cost_model.py`, `optimizer.py`, `run.py`, `operational.py`, `repository.py`,
  `state.py`, `reservation.py`, `transaction.py`, `staffing.py`,
  `staffing_service.py`, `housekeeping.py`, `postop_windows.py`,
  `post_operative.py`
- `hospital_operations/integrations/`: `marley.py`, `erpnext.py`, `hrms.py`
- `hospital_operations/utils/`: `audit.py`, `config.py`, `roles.py`
- `hospital_operations/hospital_operations/doctype/`: 12 custom DocTypes
- `hospital_operations/tests/`: pure unit tests, Frappe integration tests,
  concurrency tests, API/permission tests and synthetic factories

### Discovery and documentation

- `tools/remote_client.py`, `tools/inspect_remote.py`,
  `tools/test_remote_client.py`
- `artifacts/frappe_schema_report.json`
- `docs/01-existing-frappe-schema-analysis.md`
- `docs/02-domain-model-and-schema.md`
- `docs/03-concurrency-and-transactions.md`
- `docs/04-operational-allocation-algorithm.md`
- `docs/05-api-contracts.md`
- `docs/06-deployment.md`
- `docs/07-test-results.md`
- `docs/implementation-plan.md`, `README.md`
- `docs/08-final-implementation-report.md` (this report)

No upstream Frappe, ERPNext, Marley/Healthcare or HRMS source file was modified.

---

## 3. Existing Frappe features reused

| Requirement | Reused record/model |
|---|---|
| Patient identity | Marley `Patient` |
| Admission workflow | Marley `Inpatient Record` status lifecycle |
| Bed occupancy truth | `Inpatient Occupancy` child rows with `check_out IS NULL` |
| Physical bed / ward | Marley `Healthcare Service Unit` tree |
| Ward category and tariff | `Healthcare Service Unit Type` (`inpatient_occupancy`, `is_billable`, `item`, `rate`) |
| OT source reference | Marley `Clinical Procedure` (optional reference, not modified) |
| Billing | ERPNext `Sales Invoice`, `Sales Invoice Item`, `Item` |
| Staff identity | HRMS `Employee` |
| Staff scheduling | HRMS `Shift Assignment`, `Shift Type`, `Attendance`, `Leave Application`, `Employee Checkin` |
| Auth and permissions | Frappe session/token authentication, roles and DocPerm |

---

## 4. Schema changes

The custom app adds, and only adds, the following on the target site:

- 12 custom DocTypes: Bed Allocation Request, Bed Clinical Requirement, Bed
  Capability, Bed Operational State, Bed Reservation, Bed Staffing Snapshot, Bed
  Allocation Policy, Bed Allocation Run, Bed Allocation Proposal, Bed Allocation
  Event, Housekeeping Task, Ward Nurse Assignment.
- Five operational roles: Bed Manager, Admission Coordinator, Nursing
  Supervisor, Housekeeping Staff, Hospital Operations Administrator.
- Three additive custom fields on `Healthcare Service Unit`: `ho_ward`,
  `ho_is_allocatable`, `ho_care_level`.
- Eight query indexes and a generated `active_immediate_claim` column with a
  unique index to prevent two active immediate claims on one bed.

There were no schema changes in Phases 4–7. No patient, admission, occupancy or
billing model is duplicated.

---

## 5. API endpoints

All endpoints live under `hospital_operations.api` and require authentication.
Reads are GET-only; mutations are POST-only.

| Method | Endpoint | Allowed roles |
|---|---|---|
| GET | `beds.list` | Bed Manager, Admission Coordinator, Nursing Supervisor, Operations Admin |
| GET | `beds.availability` | Same clinical roles |
| POST | `requests.create` | Same clinical roles |
| GET | `requests.list` | Same clinical roles |
| POST | `allocation.optimize` | Same clinical roles |
| GET | `allocation.get_result` | Same clinical roles |
| POST | `allocation.confirm` / `cancel` / `transfer` / `discharge` | Same clinical roles |
| POST | `housekeeping.start` / `complete` / `verify` / `cancel` | Housekeeping Staff, Bed Manager, Nursing Supervisor, Operations Admin |
| GET | `staffing.capacity` | Bed Manager, Nursing Supervisor, Operations Admin |
| POST | `post_operative.create` / `reschedule` / `cancel` / `confirm` | Clinical roles |
| POST | `post_operative.preempt` | Bed Manager, Nursing Supervisor, Operations Admin |
| GET | `health.status` | Authenticated users |

Payloads, response shapes, pagination and error codes are in
`docs/05-api-contracts.md`.

---

## 6. Security considerations

- Guest access is denied on every clinical endpoint; role checks are enforced
  server-side before any query or mutation.
- No endpoint uses unsafe `ignore_permissions` shortcuts for caller-driven
  writes. Internal system inserts use Frappe APIs and are covered by tests.
- Pagination is bounded (default 50, maximum 200) to prevent unbounded lists.
- Request creation and reservation confirmation support idempotency keys;
  reusing a key for a different patient or window is rejected.
- Manual pre-emption requires Emergency priority and a clinical approval
  reference and is restricted to senior operational roles.
- API responses expose identifiers and operational state, not unnecessary
  clinical or patient details.
- TLS verification remains enabled for all remote discovery. Secrets are read
  only from environment variables and are never logged or committed.
- Billing failure cannot fail or alter a clinical bed assignment; billing
  helpers are read-only.

---

## 7. Tests executed and results

Full local Bench suite:

```bash
bench --site hospital.localhost run-tests --app hospital_operations
```

**Result: 150/150 passing in 7.177s.**

| Suite | Tests |
|---|---:|
| State machine | 7 |
| Clinical eligibility | 22 |
| Cost model | 9 |
| Optimiser | 10 |
| Reservations | 11 |
| Concurrency | 2 |
| DocTypes | 4 |
| Roles | 2 |
| Staffing (pure) | 17 |
| Staffing / HRMS integration | 10 |
| Housekeeping | 11 |
| Post-op windows (pure) | 7 |
| Post-operative reservations | 12 |
| Billing integration | 6 |
| API contracts | 11 |
| API permissions | 9 |
| **Total** | **150** |

Additional checks:

- Outside-Bench discovery: 73 tests discovered, 62 passed, 11 skipped
  (Frappe- and OR-Tools-dependent modules).
- `uvx ruff check hospital_operations tools` — passed.
- `compileall` for the app — passed.
- `install.validate_installation` — `ok: true`.
- `api.health.status` — DocTypes ready, active-claim constraint present.

Test coverage includes clinical hard constraints, deterministic optimisation,
double-allocation races, rollback, stale proposals, leave/absence/competency/
ratio/shift transitions, housekeeping gates, post-op delay/cancel/no-ICU/
pre-emption, billing read-only behaviour, API contracts and cross-role denial.

---

## 8. Performance benchmarks

Local synthetic benchmark (`hospital.localhost`, 2026-11-11, 12 iterations,
5 concurrent rounds; milliseconds):

| Operation | p50 | p95 | p99 |
|---|---:|---:|---:|
| Bed availability query | 3.802 | 4.434 | 4.451 |
| Clinical eligibility evaluation | 3.702 | 4.076 | 4.279 |
| Allocation optimisation | 0.280 | 0.402 | 0.509 |
| Staffed capacity computation | 2.403 | 3.921 | 4.550 |
| Reservation confirmation (full Marley admission) | 27.929 | 42.531 | 43.621 |
| Concurrent reservation attempts | 62.674 | 74.910 | 76.980 |

The concurrent test ran 5 rounds with two racers per bed: exactly one winner per
round, confirming database-level protection against double allocation. These
numbers are local synthetic measurements, not a production SLA.

---

## 9. Known limitations

- Installed and validated on the local site only. The remote production site
  requires Bench shell access, a backup and an approved deployment window.
- No real beds, patients, employees, policies, capabilities or invoices have
  been seeded.
- Clinical staffing ratios, competencies and minimum nurse counts are policy
  inputs and require hospital clinical approval.
- Room-sharing rules are not implemented.
- A post-operative hold blocks its bed immediately for the whole window; long
  holds reduce short-term capacity. CP-SAT scheduling was intentionally not
  introduced.
- Cancelled housekeeping tasks do not release a bed; reconciliation or a
  replacement task is required.
- ERPNext Sales Invoices have no direct Inpatient Record link; billing helpers
  provide patient-level reference data and reuse Marley pending-invoice rules.
- Benchmark figures are local and synthetic.
- The custom frontend, real-data seeding, production deployment and strategic
  capacity optimisation remain outside the implemented backend scope.

---

## 10. Deployment and rollback

Deployment instructions, backup requirements, validation commands and rollback
are in `docs/06-deployment.md`.

Summary:

1. Take a `bench backup --with-files` and record the paths.
2. `bench get-app` the `bff_development` branch, then
   `bench --site <site> install-app hospital_operations`.
3. Run `bench --site <site> migrate` only as part of an approved migration
   window.
4. Validate with `install.validate_installation`, `api.health.status` and the
   full app test suite.

Rollback:

```bash
bench --site <site> uninstall-app hospital_operations
bench remove-app hospital_operations
```

`before_uninstall` removes the additive `Healthcare Service Unit` fields. The
five roles are intentionally retained. Restore the pre-deployment backup if data
must be recovered.

---

## 11. Verification evidence

| Check | Result |
|---|---|
| Full Bench suite | 150/150 passing, 7.177s |
| Outside-Bench discovery | 73 discovered, 62 passed, 11 skipped |
| Ruff | passed |
| Compileall | passed |
| Installation validation | `ok: true` |
| Health probe | DocTypes ready; active-claim constraint present |
| Remote/production data | untouched |
| Schema migrations | none run |
| Git commits | none made |

## 12. Remaining work

1. Obtain approval and Bench access for production installation.
2. Approve and load real ward/bed/capability/policy/staffing configuration.
3. Run production-backed performance, security and acceptance testing.
4. Build the deferred custom frontend against `docs/05-api-contracts.md`.
5. Establish monitoring, backup and operational runbooks for the live site.
