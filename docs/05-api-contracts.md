# API Contracts — Phase 7

**Namespace:** `hospital_operations.api`
**Authentication:** Frappe token or session authentication. All endpoints reject
`Guest` and users without an operational role.
**Base URL:** `https://<site>/api/method/hospital_operations.api.<module>.<function>`

This phase exposes the backend only. A future custom frontend calls these
methods; the API never replaces Marley patient, admission, occupancy or ERPNext
billing records.

---

## 1. Conventions

| Item | Contract |
|---|---|
| Mutations | `POST` only, except `health.status` and read endpoints (`GET`). |
| Authentication | `Authorization: token <api_key>:<api_secret>` or a logged-in session. |
| Roles | Enforced server-side with `frappe.only_for`-equivalent role checks. `System Manager`, `Healthcare Administrator` and `Hospital Operations Administrator` are administrative roles. |
| Pagination | `limit` default `50`, maximum `200`; `start` is zero-based. Responses include `total`, `limit`, `start`, `has_next`. |
| Idempotency | Request creation supports `idempotency_key`; allocation confirmation and post-op confirmation are idempotent on retry. Reusing a request key for another patient is rejected. |
| Errors | Frappe exception responses with `exc_type`; `400` validation/business error, `401` unauthenticated, `403` permission denied, `404` missing record, `409` bed/reservation conflict, `417` framework validation. |
| Patient data | List APIs expose identifiers only. Clinical requirement detail is returned only from request creation. No patient name, diagnosis or notes are logged. |
| Permissions | No clinical API uses `ignore_permissions`. Internal service writes use Frappe ORM and the role boundary is enforced at the API entry point. |

---

## 2. Endpoint Summary

| Method | Endpoint | Allowed roles |
|---|---|---|
| GET | `beds.list` | Bed Manager, Admission Coordinator, Nursing Supervisor, Hospital Operations Administrator |
| GET | `beds.availability` | same as `beds.list` |
| POST | `requests.create` | same as `beds.list` |
| GET | `requests.list` | same as `beds.list` |
| POST | `allocation.optimize` | same as `beds.list` |
| GET | `allocation.get_result` | same as `beds.list` |
| POST | `allocation.confirm` | same as `beds.list` |
| POST | `allocation.cancel` | same as `beds.list` |
| POST | `allocation.transfer` | same as `beds.list` |
| POST | `allocation.discharge` | same as `beds.list` |
| POST | `housekeeping.start` | Housekeeping Staff, Bed Manager, Nursing Supervisor, Hospital Operations Administrator |
| POST | `housekeeping.complete` | same as `housekeeping.start` |
| POST | `housekeeping.verify` | same as `housekeeping.start` |
| POST | `housekeeping.cancel` | same as `housekeeping.start` |
| GET | `staffing.capacity` | Bed Manager, Nursing Supervisor, Hospital Operations Administrator |
| GET | `health.status` | Any authenticated non-Guest user |
| POST | `post_operative.create` | Bed Manager, Admission Coordinator, Nursing Supervisor, Hospital Operations Administrator |
| POST | `post_operative.reschedule` | same as `post_operative.create` |
| POST | `post_operative.cancel` | same as `post_operative.create` |
| POST | `post_operative.confirm` | same as `post_operative.create` |
| POST | `post_operative.preempt` | Bed Manager, Nursing Supervisor, Hospital Operations Administrator (not Admission Coordinator) |

`post_operative.preempt` is the only manual-override endpoint. It requires an
`Emergency` request and a non-empty `clinical_approval_reference`. Admission
Coordinators cannot preempt a hold.

---

## 3. Beds

### `GET beds.list`

Query parameters: `ward`, `care_level`, `state` (comma-separated), `allocatable`
(boolean), `limit`, `start`.

Response:

```json
{
  "beds": [
    {
      "name": "ICU-BED-01",
      "healthcare_service_unit_name": "ICU Bed 01",
      "ward": "ICU",
      "care_level": "ICU",
      "allocatable": true,
      "state": "READY",
      "state_version": 3,
      "occupied": false,
      "active_reservation": false
    }
  ],
  "total": 1,
  "limit": 50,
  "start": 0,
  "has_next": false
}
```

### `GET beds.availability`

Without `request`, returns beds that are allocatable, `READY`, unoccupied and
unreserved. With `request`, evaluates Stage 1 clinical eligibility for that
request and includes structured rejection reasons.

```json
{
  "request": "BAR-0001",
  "beds": [
    {
      "request": "BAR-0001",
      "bed": "ICU-BED-03",
      "eligible": false,
      "reasons": [
        {"code": "CARE_LEVEL_MISMATCH", "message": "Bed care level 'General' does not match request care level 'ICU'"}
      ],
      "matched_requirements": [],
      "optional_gaps": []
    }
  ],
  "eligible_count": 0,
  "total": 1,
  "limit": 50,
  "start": 0,
  "has_next": false
}
```

---

## 4. Allocation Requests

### `POST requests.create`

| Field | Required | Notes |
|---|---|---|
| `patient` | yes | Existing `Patient` |
| `inpatient_record` | no | If supplied, must belong to `patient` |
| `source` | no | `ED`, `OPD`, `OT`, `Transfer`, `Direct Admission` |
| `priority` | no | `Emergency`, `Urgent`, `Routine` |
| `care_level` | no | `ICU`, `General`, `Special`, `Other` |
| `requested_at` | no | Datetime; defaults to now |
| `requested_by` | no | Defaults to the session user |
| `medical_department` | no | Medical Department link |
| `clinical_approval_reference` | no | Required by clinical policy for overrides |
| `idempotency_key` | no | Generated if omitted; reuse returns the existing request for the same patient |
| `requirements` | no | JSON list or native list of `Bed Clinical Requirement` rows |
| `notes` | no | Free text; never logged in audit details |
| `company` | no | Defaults to the site default company |

Requirement row fields: `requirement_type`, `operator`, `required_value`,
`mandatory`, `clinical_reason`.

Response: request summary plus the normalized requirement rows. Creates a
`REQUEST_CREATED` audit event.

### `GET requests.list`

Filters: `patient`, `inpatient_record`, `status` (comma-separated), `priority`,
`source`, `care_level`, `limit`, `start`. Returns identifiers and workflow state
only; patient names and notes are omitted.

---

## 5. Allocation

### `POST allocation.optimize`

Body: `request_names` (comma string or list), `policy`, `bed_names` (optional).
Calls the OR-Tools min-cost-flow optimiser, persists `Bed Allocation Run` and
proposals, and marks requests `PROPOSED` or `WAITLISTED`.

Response includes `run`, `run_id`, `status`, counts, duration and proposal
summaries.

### `GET allocation.get_result`

Query: `run` (required), `limit`, `start`. Returns sanitized run metadata and
paginated proposals. An unknown run raises `ValidationError`.

### `POST allocation.confirm`

Body: `proposal`, `idempotency_key`, `expected_state_version`. Revalidates
eligibility, locks the bed, admits through Marley and marks the bed `OCCUPIED`.
Duplicate confirmation returns the original result.

### `POST allocation.cancel`

Body: `proposal`, `reason`. Only `PROPOSED` proposals can be cancelled.

### `POST allocation.transfer`

Body: `inpatient_record`, `to_bed`, `allocation_request`, optional `from_bed`,
optional `idempotency_key`. Uses Marley transfer and starts cleaning on the old
bed.

### `POST allocation.discharge`

Body: `inpatient_record`, optional `idempotency_key`. Uses Marley discharge and
starts cleaning on every released bed. Billing is not a prerequisite for
clinically urgent placement.

---

## 6. Housekeeping

### `POST housekeeping.start`

Body: `task`, optional `assigned_employee`, optional `reason`. Moves
`PENDING → IN_PROGRESS` and the bed to `CLEANING_IN_PROGRESS` (or
`ISOLATION_CLEANING`).

### `POST housekeeping.complete`

Body: `task`, optional `reason`. Moves `IN_PROGRESS → COMPLETED`; the bed stays
unavailable.

### `POST housekeeping.verify`

Body: `task`, optional `verified_by`, optional `reason`. Moves
`COMPLETED → VERIFIED` and releases the bed to `READY`.

### `POST housekeeping.cancel`

Body: `task`, optional `reason`. Cannot cancel a `VERIFIED` task; the bed stays
in its cleaning state and must be reconciled or replaced.

All four return the task name, bed, type, status and timestamps.

---

## 7. Staffing

### `GET staffing.capacity`

Query: `ward` or `wards` (comma-separated), optional `at` (datetime). When no
ward is supplied, all wards with allocatable beds are returned.

```json
{
  "at": "2026-11-11 09:00:00",
  "policy_version": "1",
  "wards": {
    "ICU": {
      "ward": "ICU",
      "care_level": "ICU",
      "enabled": true,
      "available_nurses": 4,
      "qualified_nurses": 3,
      "current_patients": 5,
      "physical_beds": 12,
      "staffed_bed_capacity": 9,
      "available_capacity": 4,
      "shift": "Day",
      "policy_version": "1",
      "exclusions": {},
      "notes": {}
    }
  }
}
```

If the active allocation policy has no `staffing` block, `enabled` is `false`
and the eligibility engine does not block on staffing.

---

## 8. Post-Operative Reservations

### `POST post_operative.create`

Body: `allocation_request` (source `OT`), `bed`, optional `start_at`,
`expires_at`, `hold_hours` (default 4), `idempotency_key`, `reason`. The bed
becomes `RESERVED` immediately and the request becomes `RESERVED`. Overlapping
`ACTIVE` holds for the same bed are rejected under a row lock.

### `POST post_operative.reschedule`

Body: `reservation`, `start_at`, optional `expires_at`, `hold_hours`, `reason`.
Revalidates the new half-open window against other active holds.

### `POST post_operative.cancel`

Body: `reservation`, optional `reason`. Cancels an active hold, releases the bed
when no other active hold remains, and cancels the OT request.

### `POST post_operative.confirm`

Body: `reservation`, optional `idempotency_key`, `from_bed`. Revalidates the bed,
admits a scheduled patient or transfers an admitted patient, and marks the target
bed `OCCUPIED`. Duplicate confirmation is idempotent.

### `POST post_operative.preempt`

Body: `reservation`, `emergency_request`, `clinical_approval_reference`,
optional `reason`. Requires an `Emergency` emergency request and a non-empty
approval reference. Cancels the hold, waitlists the OT request, releases the bed
and writes a `MANUAL_OVERRIDE` audit event.

---

## 9. Health

### `GET health.status`

Authenticated readiness probe. Returns app version, site, timestamp,
`doctypes_ready` and `active_claim_constraint`. It returns no patient data and
no secrets.

---

## 10. Permission Matrix

| Capability | Bed Manager | Admission Coordinator | Nursing Supervisor | Housekeeping Staff | HO Administrator |
|---|---:|---:|---:|---:|---:|
| Read beds / availability | yes | yes | yes | no | yes |
| Create/list requests | yes | yes | yes | no | yes |
| Optimise / confirm / cancel | yes | yes | yes | no | yes |
| Transfer / discharge | yes | yes | yes | no | yes |
| Housekeeping start/complete/verify/cancel | yes | no | yes | yes | yes |
| Staffing capacity | yes | no | yes | no | yes |
| Post-op create/reschedule/cancel/confirm | yes | yes | yes | no | yes |
| Post-op preempt (manual override) | yes | no | yes | no | yes |

Tests create isolated users for each role and verify both allow and deny paths.
Missing/invalid tokens are rejected by Frappe before the method body runs.

---

## 11. Known Limitations

- `Sales Invoice` has no direct `Inpatient Record` link; billing helpers return
  patient-linked reference data and a documented limitation.
- The API is backend-only; there is no custom frontend in this phase.
- Post-operative holds block the physical bed for the whole reserved window.
- `beds.availability` live staffing uses the active allocation policy. If that
  policy has no staffing block, staffing does not filter beds.
