# Concurrency and Transactions (Phases 4–7)

**Scope:** transaction-safe bed claims, Marley admission/transfer/discharge
boundaries, idempotency, stale-proposal rejection, expiry/reconciliation, and
the housekeeping gate that protects bed release.

**Status:** implemented and tested on the local Bench site
`hospital.localhost`. No production or remote site was modified.

---

## 1. Invariants

1. A physical `Healthcare Service Unit` can have at most one active immediate
   claim.
2. A bed is never treated as available while it has an active Marley occupancy
   or an active reservation.
3. A bed becomes `OCCUPIED` only after Marley records the occupancy.
4. A bed released by discharge or transfer enters `CLEANING_REQUIRED`; it never
   jumps directly to `READY`.
5. Allocation writes, reservation writes, Marley ADT writes and audit events
   either all survive or all roll back.
6. A stale proposal never becomes an admission.
7. No code path calls `frappe.db.commit()` inside a reusable service.

---

## 2. Database guarantees

`Bed Reservation` has a generated stored column:

```text
active_immediate_claim =
    healthcare_service_unit
    when status = 'ACTIVE' and reservation_type = 'IMMEDIATE'
    else NULL
```

A unique index `uq_bres_active_immediate_claim` enforces one active immediate
claim per bed in MariaDB. Application checks are not the only defense: the
database rejects the second row even if application code is bypassed.

`POST_OPERATIVE` holds are not covered by that generated column because multiple
historical rows and non-overlapping windows are valid. Their guarantee comes from
the `RESERVED` operational state plus overlap queries under the HSU lock. The
existing `(healthcare_service_unit, status)` and request/idempotency indexes
support the lock and lookup paths. `Bed Operational State` is unique per service
unit, and its `version` column is the optimistic token used by proposals.

---

## 3. Isolation and lock order

The site runs MariaDB with `tx_isolation = REPEATABLE-READ` and
`autocommit = 0`. A plain `SELECT` therefore establishes a snapshot and can keep
returning old rows after another transaction commits. For that reason every
mutation uses **current reads** (`SELECT ... FOR UPDATE`) before any ordinary
ORM read.

Lock order is fixed and documented:

```text
Bed Allocation Proposal (confirm/cancel)
        ↓
Bed Allocation Request
        ↓
Inpatient Record
        ↓
Healthcare Service Unit(s)  (sorted)
        ↓
Bed Operational State(s)    (same bed order)
```

Housekeeping mutations use the same discipline, with the task locked first:

```text
Housekeeping Task (start/complete/verify/cancel)
        ↓
Healthcare Service Unit
        ↓
Bed Operational State
```

`ensure_task` locks the HSU and state, checks for an open task, then inserts;
the `(healthcare_service_unit, status)` index keeps that check cheap. Each
operation runs in its own savepoint (`ho_housekeeping_create/start/complete/
verify/cancel`).

Post-operative holds use the same discipline:

```text
create:      Bed Allocation Request -> Healthcare Service Unit -> State
             -> overlapping ACTIVE Bed Reservation rows (FOR UPDATE)
reschedule:  Bed Reservation -> Bed Allocation Request -> HSU -> State
             -> overlapping ACTIVE Bed Reservation rows (FOR UPDATE, excluding self)
confirm:     Bed Reservation -> Bed Allocation Request -> Inpatient Record
             -> HSU(s) (sorted) -> State(s) -> other ACTIVE holds (FOR UPDATE)
```

Windows are half-open, so adjacent holds do not conflict. A successful creation
marks the bed `RESERVED` immediately; confirmation requires that operational
state to still be `RESERVED` and rechecks clinical eligibility after every lock.
Expiry and preemption release the bed only when no other active reservation
remains.

Transfer and discharge read the active occupancy with `FOR UPDATE` before
locking the old and new beds. Expiry and reconciliation select candidates with
`FOR UPDATE`, then lock the reservation, bed and state before reading them
through the ORM. Sorting bed names prevents two flows from acquiring the same
beds in opposite order.

The row locks in `transaction.py`:

- `lock_documents(doctype, names)` — lock one or more documents in sorted order.
- `lock_document_values(doctype, name, fields)` — current read returning the
  values needed to choose the next lock.
- `lock_beds(names)` — lock physical beds.
- `lock_bed_states(names)` — lock operational state rows; missing state rows are
  tolerated because the bed row itself is already locked.
- `lock_inpatient_record(name)` — serialize ADT work for one inpatient.

A missing row during locking is a `ReservationConflict` (HTTP 409), not a silent
success.

---

## 4. Transaction boundary

Each public service operation runs inside `savepoint(...)`:

```python
with savepoint("ho_confirm"):
    lock proposal, request, inpatient, bed, state
    recheck eligibility and version
    create reservation / change state / call Marley
    write audit events
```

`savepoint()` uses `frappe.db.savepoint`, rolls the nested work back on any
exception, and never commits. The outer Frappe request transaction remains
responsible for commit or rollback. A failed Marley validation therefore leaves
no reservation, no bed-state change, no request status change and no partial
audit event.

### Marley boundary

`integrations/marley.py` is the only write boundary to Marley. It wraps:

- `InpatientRecord.admit`
- `InpatientRecord.transfer`
- `check_out_inpatient` + `discharge_patient`

The wrapper validates the record status and verifies the resulting active
occupancy. Marley and `hospital_operations` share the same MariaDB connection
and request transaction, so these writes participate in the same savepoint.
Marley's own billing/nursing-task validations still apply. If Marley cannot
participate in a future external workflow, the refund/recovery boundary must be
documented before enabling that workflow.

---

## 5. Allocation confirmation

`reservation.confirm(proposal, idempotency_key=None,
expected_state_version=None)`:

1. Current-read the proposal. If it is already `CONFIRMED`, return an
   idempotent result.
2. Current-read the request and require `inpatient_record`.
3. Lock the inpatient record, proposed bed and bed state.
4. Reject a proposal that is not `PROPOSED`, or a request that is not
   `PROPOSED`/`RESERVED`.
5. Look up the idempotency key. A completed confirmation returns the existing
   result; an `ACTIVE` reservation resumes the interrupted Marley step.
6. Compare the current bed-state version with the explicit expectation or the
   proposal's `bed_state_version`; mismatch is `StaleProposal` (409).
7. Re-evaluate mandatory Stage 1 eligibility against the freshly locked bed.
8. Create the `ACTIVE` immediate reservation, mark the bed `RESERVED`, set the
   request to `RESERVED`, and write `RESERVATION_CREATED`.
9. Call Marley `admit`, then mark the bed `OCCUPIED`, confirm the reservation,
   mark the proposal `CONFIRMED`, set the request to `ALLOCATED`, and write
   `ADMISSION_LINKED` plus `RESERVATION_CONFIRMED`.

The default idempotency key is `confirm:<proposal>`. Supplying a stable caller
key is recommended for retries after a timeout.

### Stale proposals

The proposal snapshot contains the bed-state version seen by the optimiser. A
different reservation, maintenance action or occupancy change increments that
version. Confirmation then fails with `StaleProposal` instead of silently
allocating a bed that changed after the proposal was generated.

### Duplicate confirmation

Repeating `confirm` with the same proposal/key returns the stored result with
`idempotent: true`. It does not create a second reservation or occupancy.

---

## 6. Cancel, transfer and discharge

### Cancel

`reservation.cancel(proposal)` is allowed only for a `PROPOSED` proposal. It
cancels an `ACTIVE` reservation for that request/bed, releases a `RESERVED` bed
to `READY` only when no active claim remains, sets both proposal and request to
`CANCELLED`, and writes `RESERVATION_CANCELLED`. Repeated cancels are
idempotent. A `CONFIRMED` proposal cannot be cancelled through this method.

### Transfer

`reservation.transfer(inpatient_record, to_bed, allocation_request, from_bed,
idempotency_key)`:

- verifies that the request belongs to the inpatient;
- locks the request, inpatient, current bed, target bed and both states;
- rechecks the current active bed and Stage 1 eligibility for the target;
- creates a `TRANSFER` reservation and marks the target `RESERVED`;
- calls Marley `transfer`;
- marks the old bed `VACATED` then `CLEANING_REQUIRED`;
- marks the target `OCCUPIED`, confirms the reservation and sets the request
  `ALLOCATED`;
- writes `TRANSFER_INITIATED` and `TRANSFER_COMPLETED`.

### Discharge

`reservation.discharge(inpatient_record)` locks the inpatient and all active
beds, calls Marley `check_out_inpatient` + `discharge_patient`, then moves every
released bed through `VACATED` to `CLEANING_REQUIRED`. It writes `BED_RELEASED`
and `DISCHARGE_INITIATED`. It does **not** make a bed `READY`; housekeeping
verification is a separate gate. Repeated discharge is idempotent when Marley
already reports `Discharged` with no active occupancy.

---

## 7. Expiry and reconciliation

### Expiry

`tasks.expire_stale_reservations` (hourly) selects `ACTIVE` reservations whose
`expires_at` is in the past using `FOR UPDATE`. For each row it locks the bed and
state, marks the reservation `EXPIRED`, releases the bed to `READY` only if no
active claim remains, sets the request `WAITLISTED`, and writes
`RESERVATION_EXPIRED`. Expired keys remain in the table; they are not reused.

### Reconciliation

`tasks.reconcile_bed_reservations` (daily) repairs two cases:

1. `ACTIVE` reservations whose request is missing or cancelled are cancelled and
   their beds released.
2. A bed left in `RESERVED` with no `ACTIVE` reservation is returned to `READY`.

Both paths lock the reservation, bed and state before reading, are idempotent,
and write audit events. The existing staffing-snapshot cleanup is unchanged.

---

## 8. Retry and conflict behavior

| Condition | Result | Caller action |
|---|---|---|
| Proposal already confirmed, same key | Success, `idempotent: true` | Treat as success |
| Bed changed after proposal | `StaleProposal`, HTTP 409 | Regenerate a proposal |
| Bed already occupied/reserved, ineligible | `ReservationConflict`, HTTP 409 | Choose another bed |
| Missing/invalid document or argument | `ReservationError`/validation, HTTP 400 | Correct the request |
| Marley validation failure | `MarleyError`, HTTP 400 | Fix Marley prerequisites; the savepoint rolled back |
| Lock wait timeout / deadlock | MariaDB `OperationalError` | Retry the whole request with the same idempotency key |

The server does not automatically retry a mutation, because a blind retry can
duplicate an ADT action. Clients should retry timeout/conflict responses with the
same idempotency key. Scheduled expiry and reconciliation jobs are safe to rerun.

---

## 9. API surface

All mutation endpoints are `POST`-only, authenticated, and role-checked for
Bed Manager, Admission Coordinator, Nursing Supervisor or Hospital Operations
Administrator:

```text
hospital_operations.api.allocation.confirm
hospital_operations.api.allocation.cancel
hospital_operations.api.allocation.transfer
hospital_operations.api.allocation.discharge
```

`ReservationConflict` maps to HTTP 409 and `ReservationError` to HTTP 400 via
`frappe.throw`. Guest access is rejected. Responses contain proposal, request,
bed and status identifiers only; no clinical details are returned.

---

## 10. Test evidence

Command:

```bash
bench --site hospital.localhost run-tests --app hospital_operations
```

Result: **130 tests passed** (7.615 s), including:

- 11 reservation tests: confirm, duplicate confirm, competing claim, stale
  version, failed Marley rollback, expiry, cancel, transfer, discharge,
  reconciliation and key retention.
- 2 concurrency tests: two real threads confirming different proposals for one
  bed (exactly one winner, one `ReservationConflict`, one occupancy) and a raw
  SQL insert proving the unique active-claim constraint.
- 17 pure staffing tests: rule parsing, presence sources, day/overnight shift
  windows, ratio/competency/minimum staffing, leave/absence/ward exclusions.
- 10 HRMS integration tests: present/absent/leave/competency/other-ward,
  shift transition, check-in presence, occupancy workload, snapshot persistence
  and no-policy behaviour.
- 11 housekeeping tests: auto task on discharge/transfer, start/complete/verify,
  direct `READY` rejection, isolation cleaning, cancel, idempotent ensure and
  reconciliation.
- 7 pure post-operative window tests: duration validation, half-open adjacency,
  partial/contained overlap, disjoint windows and default hold duration.
- 12 post-operative integration tests: hold creation/idempotency/overlap,
  cleaning/no-ICU rejection, reschedule delay/conflict, cancellation,
  clinically approved emergency preemption, admission, transfer, duplicate
  confirmation, blocked-bed revalidation and expired holds.
- 6 billing integration tests: non-billable and billable tariffs, occupancy
  periods, clear/unbilled discharge reconciliation and patient-linked invoice
  reads without writes.
- State, eligibility, cost, optimiser, DocType and role tests.

`ruff` and `compileall` also pass. No schema change was required for Phases 4–7,
so no migration was run.

---

## 11. Known limits at this gate

- A `POST_OPERATIVE` hold blocks its bed immediately for the whole window, even
  when surgery is hours away. This protects the guarantee but reduces short-term
  capacity; hospitals must choose hold windows deliberately.
- Post-operative delays are handled by `reschedule`; if a delayed case cannot
  move and its hold expires, `expire` releases the bed and waitlists the request.
  There is no automatic CP-SAT rescheduler.
- There is no automatic client retry loop; callers must use idempotency keys.
- Marley's external billing/nursing validations remain part of the same
  transaction; a future external service would need a documented outbox or
  reconciliation boundary. ERPNext billing helpers are read-only and are not a
  prerequisite for clinical placement.
- Housekeeping completion/verification is implemented; verification is the only
  path that returns a cleaning bed to `READY`.
- Staffing capacity is computed live from HRMS signals; `Bed Staffing Snapshot`
  is reporting evidence, not the admission gate. Malformed staffing policy JSON
  raises a validation error rather than silently relaxing the constraint.
- The local test suite uses synthetic records only. No production data was
  touched.
