# Test Results — Phases 1–7

**Date:** 2026-11-11
**Environment:** local Bench at `/home/satish/Documents/frappe_bench` with site
`hospital.localhost`, plus a standalone validation venv for OR-Tools unit tests.
No `bench migrate` and no remote/production changes were made. The remote site
was used only for Phase 0 read-only discovery.

## 1. Executed outside Bench

| Area | Tests | Result |
|---|---:|---|
| DocType JSON validation | 12/12 | **PASS** |
| Structural integrity | all | **PASS — 0 problems** |
| Python compilation (`compileall`) | all modules | **PASS** |
| Ruff (`F`, `I`; line length 99) | all app/tool files | **PASS** |
| Full local unittest discovery (system Python) | 73 | **62 passed, 11 skipped** |
| State machine | 7 | **PASS** |
| Clinical eligibility | 22 | **PASS** |
| Cost model | 9 | **PASS** |
| Pure staffing rules | 17 | **PASS** |
| Pure post-operative windows | 7 | **PASS** |
| Min-cost-flow optimiser | 10 | **PASS** (OR-Tools venv) |

The eleven skipped items are the Frappe integration modules (`test_doctypes`,
`test_roles`, `test_reservation`, `test_concurrency`,
`test_staffing_integration`, `test_housekeeping`, `test_post_operative`,
`test_billing_integration`, `test_api`, `test_api_permissions`) and
`test_optimizer` when OR-Tools is absent from the outer interpreter. They run
inside the local Bench below (OR-Tools is installed in the Bench env).

## 2. Executed on the local Frappe site

The app was fetched from `bff_development` (commit `ccb2f6e`) and installed on
`hospital.localhost`:

```bash
bench get-app --branch bff_development https://github.com/skbasava/hospifront.git
bench --site hospital.localhost install-app hospital_operations
```

Phase 4–7 code was then synchronised from the workspace working tree into the
Bench app (`rsync`) without a new commit or migration. The remote site was not
touched.

| Check | Command | Result |
|---|---|---|
| App installed | `bench --site hospital.localhost list-apps` | **PASS** — `hospital_operations 0.1.0 bff_development` |
| Install validation | `bench --site hospital.localhost execute hospital_operations.install.validate_installation` | **PASS** — every check `true`; `ok: true` |
| Health probe | `bench --site hospital.localhost execute hospital_operations.api.health.status` | **PASS** — `doctypes_ready: true`, `active_claim_constraint: true` |
| Authenticated HTTP | `ping`, auth, versions, health | **PASS** — HTTP 200, `Administrator` |
| Local schema discovery | `tools/inspect_remote.py --target local` | **PASS** — 1,043 DocTypes, 12 Hospital Operations DocTypes, 0 API errors |
| DocType tests | `... --module hospital_operations.tests.test_doctypes` | **PASS — 4/4** |
| Role/permission tests | `... --module hospital_operations.tests.test_roles` | **PASS — 2/2** |
| OR-Tools import (Bench env) | `bench pip install "ortools>=9.8,<10"` | **PASS** — OR-Tools 9.15.6755 |

Versions: Frappe 15.126.1, ERPNext 15.124.5, HRMS 15.64.8, Healthcare 15.2.5,
hospital_operations 0.1.0. `/etc/hosts` contains `127.0.0.1 hospital.localhost`.

`validate_installation` confirmed: 12 DocTypes, five operational roles, the
three `Healthcare Service Unit` custom fields, eight indexes, the
`active_immediate_claim` column/index, the `no_duplicate_active_claims`
constraint and the required permission. No partial rows remained from the
earlier failed sync.

The first install attempt failed because
`hospital_operations/hospital_operations/` lacked `__init__.py`; adding it in
commit `ccb2f6e` fixed the namespace-package error and the retry succeeded.

`bench migrate` was **not** run: the local bench reported
`Service redis_cache is not running`. `after_install` had already created the
roles, custom fields, indexes and constraint, and `install-app` marked the
patches complete. Phases 4–7 made no schema change, so installation is unchanged.

## 3. Reservation, concurrency and later-phase suites

```bash
bench --site hospital.localhost run-tests --app hospital_operations \
  --module hospital_operations.tests.test_reservation
bench --site hospital.localhost run-tests --app hospital_operations \
  --module hospital_operations.tests.test_concurrency
bench --site hospital.localhost run-tests --app hospital_operations
```

| Suite | Tests | Result |
|---|---:|---|
| Reservation | 11 | **PASS — 11/11** |
| Concurrency | 2 | **PASS — 2/2** |
| Staffing integration | 10 | **PASS — 10/10** |
| Housekeeping | 11 | **PASS — 11/11** |
| Post-operative integration | 12 | **PASS — 12/12** |
| Billing integration | 6 | **PASS — 6/6** |
| API contracts | 11 | **PASS — 11/11** |
| API permissions/security | 9 | **PASS — 9/9** |
| Full Bench suite | 150 | **PASS — 150/150 in 7.177s** |

Reservation coverage:

- confirm reserves, calls Marley admission, marks the bed `OCCUPIED`, request
  `ALLOCATED`, proposal `CONFIRMED`;
- duplicate confirm returns an idempotent result and creates one occupancy;
- a second proposal for the same occupied bed is rejected as stale/conflict;
- an outdated `bed_state_version` is rejected before any write;
- a failed Marley admission rolls back the reservation, bed state, request and
  proposal;
- expiry releases a `RESERVED` bed to `READY`, marks the reservation `EXPIRED`
  and the request `WAITLISTED`;
- cancellation releases a reserved bed and cancels the proposal;
- transfer updates Marley occupancy, marks the old bed `CLEANING_REQUIRED` and
  the new bed `OCCUPIED`;
- discharge releases the bed to `CLEANING_REQUIRED`; a second discharge is
  idempotent;
- reconciliation repairs orphaned `RESERVED` bed states;
- an expired idempotency key remains bound to its `EXPIRED` reservation.

Concurrency coverage:

- two threads with independent Frappe connections confirm competing proposals
  for the same bed at the same time: exactly one succeeds, one gets a
  `ReservationConflict`, one occupancy and one `CONFIRMED` reservation remain;
- two raw `ACTIVE`/`IMMEDIATE` `Bed Reservation` rows for one bed prove the
  MariaDB generated-column unique constraint (`IntegrityError`).

The lock order, savepoint boundary, REPEATABLE-READ rationale and Marley
recovery boundary are documented in `docs/03-concurrency-and-transactions.md`.

## 4. HRMS and housekeeping tests (Phase 5)

```bash
python3 -m unittest hospital_operations.tests.test_staffing
bench --site hospital.localhost run-tests --app hospital_operations \
  --module hospital_operations.tests.test_staffing_integration
bench --site hospital.localhost run-tests --app hospital_operations \
  --module hospital_operations.tests.test_housekeeping
```

Pure staffing coverage:

- nested `staffing` policy parsing; absent/empty configuration disables checks;
- invalid ratio, minimum, competency and presence source are rejected;
- attendance presence incl. half-day/WFH flags; scheduled presence is explicit;
- day and overnight shift windows, including next-day early hours;
- ratio cap, physical-bed cap, minimum-nurse floor and occupied workload;
- inactive employee, approved leave, absence, off-shift, wrong ward and wrong
  competency exclusions.

HRMS integration coverage:

- present qualified nurse produces capacity from live HRMS records;
- approved leave and absent attendance exclude the nurse;
- wrong competency counts as available but not qualified;
- a nurse assigned to another ward is not counted;
- day/night shift transition changes the counted nurse;
- check-in presence source (`IN` present, later `OUT` absent);
- active Marley occupancy reduces available capacity;
- snapshot persistence stores derived counts and policy version;
- a policy without `staffing` disables capacity checks (`None`).

Housekeeping coverage:

- discharge and transfer create a pending `DISCHARGE_CLEAN` task;
- start moves the bed to `CLEANING_IN_PROGRESS` and stamps `started_at`;
- complete leaves the bed in cleaning and stamps `completed_at`;
- verify stamps `verified_by` and releases the bed to `READY`;
- a direct `mark_ready` is rejected while the latest task is not `VERIFIED`;
- isolation cleaning uses `ISOLATION_CLEAN` and `ISOLATION_CLEANING`;
- cancel leaves the bed in its cleaning state;
- ensure is idempotent (one open task per cleaning cycle);
- reconciliation creates tasks for cleaning beds that lack one.

## 5. OT and ERPNext integration tests (Phase 6)

```bash
python3 -m unittest hospital_operations.tests.test_postop_windows
bench --site hospital.localhost run-tests --app hospital_operations \
  --module hospital_operations.tests.test_post_operative
bench --site hospital.localhost run-tests --app hospital_operations \
  --module hospital_operations.tests.test_billing_integration
```

Post-operative coverage:

- creation reserves the bed and request, stores type/window and supports
  idempotent retries;
- overlapping active holds, cleaning beds, non-OT requests and invalid windows
  are rejected;
- a surgical delay reschedules the hold, and a conflicting new window is
  rejected;
- cancelled surgery releases the bed and cancels the request; duplicate cancel
  is idempotent;
- emergency preemption requires an `Emergency` request and clinical approval,
  releases the bed, waitlists the OT request and writes `MANUAL_OVERRIDE`;
- confirmation admits a scheduled patient, transfers an already admitted
  patient (cleaning the old bed), and is idempotent on retry;
- a bed blocked after the hold or an expired hold cannot be confirmed.

Billing integration coverage:

- non-billable and billable service-unit tariffs, including the generated Item;
- active occupancy periods and chargeable hours without creating invoices;
- clear discharge reconciliation for non-billable occupancy;
- unbilled billable occupancy is reported as blocking;
- admission invoice reads are patient-linked and explicitly flagged as lacking a
  direct Inpatient Record link.

## 6. API and benchmark validation (Phase 7)

```bash
bench --site hospital.localhost run-tests --app hospital_operations \
  --module hospital_operations.tests.test_api
bench --site hospital.localhost run-tests --app hospital_operations \
  --module hospital_operations.tests.test_api_permissions
bench --site hospital.localhost execute hospital_operations.benchmarks.run
```

API contract coverage:

- bed list projection/filtering and availability exclusion of blocked beds;
- request-scoped availability returns `CARE_LEVEL_MISMATCH` and other reasons;
- request create idempotency, audit event and child requirement persistence;
- reused idempotency key for another patient is rejected;
- request list pagination and filtering;
- optimize/get-result summary, pagination and unknown-run validation;
- housekeeping start/complete/verify releases the bed only after verification;
- staffing capacity response shape and live counts;
- post-op create/reschedule/cancel returns type, window and bed state.

Security matrix coverage:

- guest and users without an operational role are denied;
- Housekeeping Staff can start a task but cannot optimize;
- Admission Coordinator cannot start housekeeping or preempt a post-op hold;
- missing patient, invalid pagination and malformed requirement JSON raise
  validation errors;
- every mutating method is POST-only and every read method is GET-only,
  including `health.status`.

### Benchmark results

`hospital_operations.benchmarks.run` refuses non-development sites, creates
synthetic beds/patients/staff and cleans them up. Local run on
`hospital.localhost` (2026-11-11), 12 iterations for read paths and 5 concurrent
rounds:

| Operation | p50 ms | p95 ms | p99 ms | Notes |
|---|---:|---:|---:|---|
| Bed availability query | 3.802 | 4.434 | 4.451 | 8 ready ICU beds, live staffing |
| Clinical eligibility | 3.702 | 4.076 | 4.279 | One request against 8 beds |
| Allocation optimisation | 0.280 | 0.402 | 0.509 | OR-Tools min-cost flow, preloaded contexts |
| Staffed capacity | 2.403 | 3.921 | 4.550 | Live HRMS signals |
| Reservation confirmation | 27.929 | 42.531 | 43.621 | Includes Marley admission and audit |
| Concurrent reservation attempts | 62.674 | 74.910 | 76.980 | Two clients race one bed; one wins per round |

These are local synthetic measurements, not production SLAs. Re-run on the
target hardware with representative policy and staffing data before setting
capacity targets.

## 7. Case coverage

### State machine

- Full happy path `READY → RESERVED → OCCUPIED → VACATED → CLEANING_REQUIRED → CLEANING_IN_PROGRESS → READY`
- `OCCUPIED → READY` rejected
- `VACATED → READY` rejected (cleaning mandatory)
- `ISOLATION_CLEANING → READY` allowed
- Maintenance/blocked paths allowed
- Unknown states rejected
- Only `READY` is allocatable

### Eligibility

- Compatible ICU bed eligible; ICU↔General and General↔ICU rejected
- `Other` care level accepts any configured level
- Maintenance, blocked, vacated, both cleaning states, occupied and reserved rejected
- Non-allocatable and unknown-state beds rejected
- Ventilator mandatory requirement enforced; optional gap does not block
- Isolation accepts specific capability such as `negative_pressure`; `0` rejected
- Specialty match/mismatch, `IN` / `NOT IN`, numeric `>=`
- Accessibility true/false, room-sharing conflict, staffed-capacity exhaustion
- Case-insensitive values; structured `as_dict()` output shape

### Cost / priority

- Waiting credit is lower for longer waits; future requests get no credit
- Care-level and higher-care-level components; specialty soft penalty
- Negative/non-integer weights rejected; policy JSON parsed
- Default/custom priority order parsed; malformed orders rejected

### Optimiser

- Emergency beats routine for one bed; priority order is lexicographic
- Multiple requests competing for one bed; all compatible beds used
- No eligible bed produces a clear unassigned reason
- Soft cost cannot assign an ineligible pair
- Deterministic ties across repeated/reversed input; input hash stable
- Waiting-time preference; input hash changes when input changes
- Result shape contains algorithm, counts, hash and assignments

## 8. Deferred and out of scope

- Custom frontend (deferred by design; the backend API is ready for it).
- Production deployment and `bench migrate` on `frappe.abhidigitech.com`.
- Seeding real wards, beds, capabilities, policies and staffing data.
- Strategic bed-capacity optimisation, forecasting and simulation.
- A direct ERPNext admission ledger; billing helpers remain read-only.

## 9. Known limitations at this gate

- The app is installed and validated on the **local** site only. The remote
  `frappe.abhidigitech.com` site has not been modified; installing there needs
  shell access and approval.
- `bench migrate` has not been run on the local site; the install-time
  `after_install` hook applied the current schema. Phases 4–7 introduced no
  schema change.
- No beds, patients, employees, policies, capabilities or staffing snapshots
  have been seeded on the local site; tests create and roll back synthetic data.
- Marley admission, transfer and discharge paths are now exercised by the
  reservation tests, but only with synthetic records, not real clinical data.
- Permissions have been tested with the built-in roles, not a least-privilege
  service token.
- Room-sharing rules are still not implemented. Post-operative holds use
  half-open time windows and row locks; a hold blocks its bed immediately for the
  whole window, so long holds reduce short-term capacity.
- Staffing ratios, competencies and minimum nurse counts are policy inputs and
  need clinical approval before production use. The live gate currently uses
  submitted attendance by default; check-in mode is covered by tests.
- Housekeeping completion requires explicit verification; a cancelled task does
  not release the bed and must be reconciled or replaced.
- Billing helpers are read-only. ERPNext Sales Invoices have no direct Inpatient
  Record link, so invoice lists are patient-level reference data, not a full
  admission ledger. Discharge reconciliation reuses Marley pending-invoice rules.
- API endpoints are backend-only and have not been exercised through a browser
  frontend; token/session authentication is delegated to Frappe.
- Benchmark numbers are local synthetic measurements; production latency must be
  re-measured with real data and hardware.
- Runs above 1,000 requests are rejected rather than batched.
