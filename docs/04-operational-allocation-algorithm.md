# Operational Allocation Algorithm

**Status:** Stage 1 (mandatory clinical eligibility) implemented in Phase 2.
Stage 2 (OR-Tools min-cost flow) implemented in Phase 3 as a pure module plus
Frappe persistence. Site-backed execution still requires Phase 1 installation.

The optimiser is advisory and must never bypass Stage 1. A failed mandatory
constraint eliminates the patient-bed pair before any cost is considered.

---

## 1. Pipeline

```text
pending Bed Allocation Requests
        │
        ▼
build RequestContext (patient, care level, priority, specialty, requirements)
        │
        ▼
build BedContext for allocatable service units (batched)
        │
        ▼
Stage 1 eligibility  ──►  ineligible pairs + structured reasons
        │
        ▼
eligible pairs only
        │
        ▼
Stage 2 min-cost flow (Phase 3, implemented)
        │
        ▼
Bed Allocation Run + Proposals (never admissions)
```

The engine is a pure function of its inputs. Frappe is only used by the adapter
that loads contexts and persists runs/proposals/events.

---

## 2. Stage 1 Inputs

Implemented in `hospital_operations/hospital_operations/bed_allocation/eligibility.py`.

### RequestContext

| Attribute | Source |
|---|---|
| `name` | `Bed Allocation Request.name` |
| `care_level` | `care_level` |
| `priority` | `priority` |
| `medical_department` | `medical_department` |
| `requirements` | child `Bed Clinical Requirement` rows |

### BedContext

| Attribute | Source |
|---|---|
| `name` | `Healthcare Service Unit.name` |
| `care_level` | custom field `ho_care_level` |
| `state` | `Bed Operational State.state` (missing = `UNKNOWN`) |
| `allocatable` | custom field `ho_is_allocatable` |
| `occupied` | unresolved row in `Inpatient Occupancy` (`check_out IS NULL`) |
| `active_reservation` | `Bed Reservation` with status `ACTIVE` |
| `capabilities` | enabled, currently valid `Bed Capability` rows, grouped by type |
| `specialties` | `Bed Capability` rows of type `SPECIALTY` |
| `ward` | `ho_ward` or `parent_healthcare_service_unit` |
| `room_sharing_ok` | placeholder `True` until sharing rules land |
| `staffed_capacity_available` | latest `Bed Staffing Snapshot` for the ward, or `None` |

Loads are batched in `bed_allocation/repository.py`; no per-bed ORM query is
performed in the evaluation loop.

---

## 3. Mandatory Constraints and Reason Codes

A pair is eligible only when `reasons` is empty.

| Code | Condition |
|---|---|
| `BED_NOT_ALLOCATABLE` | `ho_is_allocatable` is false |
| `BED_MAINTENANCE` | state `MAINTENANCE` |
| `BED_BLOCKED` | state `BLOCKED` |
| `BED_NEEDS_CLEANING` | state `VACATED` / `CLEANING_REQUIRED` |
| `BED_CLEANING` | state `CLEANING_IN_PROGRESS` |
| `BED_ISOLATION_CLEANING` | state `ISOLATION_CLEANING` |
| `BED_OCCUPIED` | state `OCCUPIED` **or** authoritative occupancy row |
| `BED_RESERVED` / `BED_ACTIVE_RESERVATION` | state or active reservation |
| `BED_STATE_NOT_ALLOCATABLE` | any unknown/non-ready state |
| `STAFFED_CAPACITY_EXHAUSTED` | snapshot says no staffed bed capacity |
| `ROOM_SHARING_CONFLICT` | sharing rule failed |
| `CARE_LEVEL_MISMATCH` | care level not in configured compatibility set |
| `SPECIALTY_MISMATCH` | bed has specialties and request department is not covered |
| `REQUIREMENT_NOT_MET` | mandatory clinical requirement failed |
| `OPTIONAL_REQUIREMENT_NOT_MET` | optional requirement failed (does not block) |

Reasons are returned as `{"code", "message"}` dictionaries via
`EligibilityResult.as_dict()`. No clinical diagnosis text is copied into them.

### Clinical requirement operators

`=`, `!=`, `IN`, `NOT IN`, `>=`, `<=`.

- Values and capability types are compared case-insensitively.
- `= 1` / `= YES` treat a capability as a boolean presence flag; values such as
  `0`, `no`, `false`, `absent` are false.
- `IN` / `NOT IN` accept comma-separated values.
- `>=` / `<=` compare numerically (for example monitoring levels).
- `ICU_LEVEL` checks the bed's care level plus explicit capabilities.
- `SPECIALTY` checks explicit specialty capabilities.
- An unknown operator fails closed.

### Care-level policy

The engine accepts an optional compatibility map. The built-in map is a
conservative default:

```python
ICU     -> ICU
General -> General
Special -> Special
Other   -> any configured care level
```

Hospital-approved compatibility must be supplied from `Bed Allocation Policy`
in Phase 3; it is not hardcoded as hospital policy here.

---

## 4. Operational Readiness (Phase 2)

`bed_allocation/operational.py` provides:

- `ensure_state(bed)` — creates `READY` (or `OCCUPIED` when Marley occupancy is
  unresolved) plus an audit event.
- `set_state(bed, new_state, reason, expected_version)` — enforces the state
  machine, optimistic version check, and appends `STATE_CHANGED`.
- Wrappers: `mark_reserved`, `mark_occupied`, `mark_vacated`,
  `require_cleaning`, `start_cleaning`, `start_isolation_cleaning`,
  `mark_ready`, `block_bed`, `unblock_bed`, `mark_maintenance`.
- `initialize_allocatable_states()` — opt-in helper for newly marked beds.

`expected_version` rejects stale proposals. Row-level `SELECT ... FOR UPDATE`
and atomic Marley admission confirmation are Phase 4 work.

---

## 5. Stage 2 — Min-Cost Flow (Phase 3, implemented)

Implemented in `bed_allocation/optimizer.py`, `cost_model.py`, `priority.py`
and persisted by `bed_allocation/run.py`.

### Objective

```text
minimise  SUM(c[p,b] * x[p,b]) + SUM(u[p] * z[p])
subject to:
  SUM_b x[p,b] + z[p] = 1     for every request p
  SUM_p x[p,b] <= 1           for every bed b
  x[p,b] = 0                  for ineligible pairs
  higher clinical priority cannot be displaced by lower-priority cost
```

The network is built with OR-Tools `SimpleMinCostFlow`:

```text
source -> request (cap 1, cost 0)
request -> bed (cap 1, eligibility cost) for eligible pairs only
request -> sink (cap 1, unassigned penalty)
bed -> sink (cap 1, cost 0)
source supply = request count; sink demand = -request count
```

### Cost model

`cost_model.py` returns an integer base cost and its components:

| Component | Behaviour |
|---|---|
| `waiting` | `(cap − waiting_minutes) × weight`; longer waits cost less |
| `care_level` | penalty when care levels differ |
| `higher_care_level_use` | extra penalty when an `Other` request consumes ICU/Special |
| `specialty` | soft penalty when a department request meets an unclassified bed |
| `transfer` | flat disruption weight for `Transfer` requests (placeholder) |
| `urgency` | optional small preference; clinical priority is handled by penalties |

Weights are integers from `Bed Allocation Policy.cost_weights` (JSON). Negative
or non-integer weights are rejected. Total base cost is capped at
`1_000_000` to keep solver arithmetic bounded.

### Priority dominance and determinism

- Unassigned penalties are derived per run:
  `step = 2 × (max_assignment_cost + 1) × (request_count + 1)`.
  With order `(Emergency, Urgent, Routine)` the penalties are
  `3×step`, `2×step`, `1×step`, so one higher-priority unassigned penalises more
  than the total assignment cost of all lower-priority requests.
  A routine patient therefore cannot displace an emergency patient.
- Equal-cost solutions are broken by a stable SHA-256-derived per-pair tie
  value. Request and bed lists are sorted first, so repeated runs with the same
  input produce the same proposals and the same `input_hash`.
- Runs are capped at 1,000 requests; larger batches must be split to keep
  solver arithmetic inside 64-bit bounds.

### Persistence (advisory only)

`run.py::create_allocation_run`:

1. Loads the enabled `Bed Allocation Policy`.
2. Loads pending requests and allocatable beds.
3. Evaluates every patient-bed pair through Stage 1.
4. Calls the pure optimiser with policy weights/order and the run start time.
5. Creates one `Bed Allocation Run` (`COMPLETED`, counts, input hash,
   algorithm, policy version, duration).
6. Creates `Bed Allocation Proposal` rows with cost, priority, eligibility
   summary, explanation and the bed state version.
7. Appends one `PROPOSAL_GENERATED` audit event per proposal.
8. Marks assigned requests `PROPOSED` and unassigned requests `WAITLISTED`.

It never creates admissions, occupancy rows or invoices. Confirmation,
revalidation under lock and stale-proposal rejection are Phase 4.

### Operational notes

- OR-Tools is an optional dependency: `bench pip install "ortools>=9.8,<10"`.
- `Bed Allocation Policy.eligibility_rules` may contain
  `{"care_level_compatibility": {"ICU": ["ICU"], ...}}`; the run passes it to
  Stage 1. The repository's ad-hoc evaluation uses the conservative default.
- `input_hash` covers requests, beds, eligibility results, weights, priority
  order and the evaluation time.

---

## 6. Determinism and Testability

- Contexts are immutable dataclasses; evaluation has no database side effects.
- Requests and beds are sorted by name before solving.
- Every clinical requirement row is evaluated (exact duplicate rows are
  rejected by the request controller).
- Reason order is fixed.
- Phase 2 tests: 22 eligibility cases.
- Phase 3 tests: 9 cost/priority cases and 10 optimiser cases
  (see `docs/07-test-results.md`).

## 7. Known Limitations (through Phase 3)

1. Room-sharing data does not exist yet; the engine accepts a `False` context
   flag but the repository currently supplies `True`.
2. Active reservations are treated conservatively as blocking regardless of
   their time window; time-window conflict checks are Phase 4.
3. Staffing is enforced only when a `Bed Staffing Snapshot` exists; full HRMS
   computation is Phase 5.
4. The repository's ad-hoc `evaluate_request` uses the default care-level map;
   the persisted run path reads the policy map.
5. The repository, run service and operational service have not been executed
   against a Frappe site (no Bench/install access yet).
6. Runs above 1,000 requests are rejected rather than batched.
7. Proposals are advisory; no confirmation, lock, or stale revalidation yet.
8. Post-operative time windows / CP-SAT scheduling are not implemented.

## 8. Post-Operative Holds (Phase 6 Addendum)

Post-operative beds are not selected by the min-cost-flow optimiser. The OT
workflow creates a `Bed Allocation Request` with `source=OT`, then
`bed_allocation.post_operative.create_reservation` validates a half-open
`start_at`/`expires_at` window and places a `POST_OPERATIVE` hold.

- Mandatory clinical eligibility is evaluated at creation and again at
  confirmation; a hard constraint can never be bypassed by a window preference.
- Overlapping active holds for the same bed are rejected under row locks.
- The bed enters `RESERVED` immediately, so the postoperative guarantee takes
  precedence over short-term capacity.
- Delays use `reschedule`; cancellation and expiry release the bed only when no
  other active reservation remains.
- Emergency preemption requires an `Emergency` request and a non-empty clinical
  approval reference, writes `MANUAL_OVERRIDE`, and waitlists the OT request.
- Confirmation revalidates the bed, then uses Marley admission or transfer. It
  never writes occupancy directly.

CP-SAT is intentionally not used: this phase schedules independent single-bed
holds, not a multi-resource theatre/ICU optimisation problem. The data model is
ready for a future CP-SAT scheduler if operating-theatre slots, surgeon
availability and staffed ICU capacity must be optimised together.
