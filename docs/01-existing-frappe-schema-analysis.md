# Phase 0 — Existing Frappe Schema Analysis

**Site:** `https://frappe.abhidigitech.com`
**Discovery date:** 2026-11-10
**Mode:** Read-only (GET only). No data modified, no apps installed, no migrations run.
**Raw artifact:** `artifacts/frappe_schema_report.json`

> Credentials were read exclusively from `FRAPPE_BASE_URL`, `FRAPPE_API_KEY`,
> `FRAPPE_API_SECRET`. Values were never printed, logged or stored in this report.

---

## 1. Connectivity and Authentication

| Check | Result |
|---|---|
| Env vars present (values not printed) | `FRAPPE_BASE_URL`, `FRAPPE_API_KEY`, `FRAPPE_API_SECRET` all SET |
| `GET /api/method/frappe.auth.get_logged_user` | **HTTP 200** |
| Authenticated user | `Administrator` |
| TLS verification | Enabled (no verification bypass) |
| `GET frappe.utils.change_log.get_versions` | **HTTP 200** |

Authentication succeeded. The token currently maps to `Administrator`
(superuser), so permission results below reflect administrative access, not a
least-privilege production role.

---

## 2. Installed Application Versions

| App | Title | Branch | Version |
|---|---|---|---|
| frappe | Frappe Framework | version-15 | **15.126.0** |
| erpnext | ERPNext | version-15 | **15.124.5** |
| hrms | Frappe HR | version-15 | **15.64.8** |
| healthcare | **Marley Health** | version-15 | **15.2.5** |
| crm | Frappe CRM | version-15 | 15.0.1 |

All required components (Frappe, ERPNext, HRMS, Marley) are installed and on
`version-15`.

---

## 3. DocType Inventory

| Metric | Value |
|---|---|
| Total DocTypes visible | **1,085** |
| Healthcare (Marley) DocTypes | **106** |
| HR (HRMS) DocTypes | **111** |
| ERPNext modules present | Accounts, Stock, Selling, Buying, Manufacturing, Assets, Projects, Quality Management, Setup, etc. |

Full lists are in `artifacts/frappe_schema_report.json` under
`doctype_summary.healthcare_doctypes`, `hr_doctypes`, `payroll_doctypes`.

### Relevant Marley DocTypes confirmed

`Patient`, `Patient Encounter`, `Inpatient Record`, `Inpatient Occupancy`,
`Healthcare Service Unit`, `Healthcare Service Unit Type`, `Healthcare Settings`,
`Medical Department`, `Patient Appointment`, `Nursing Task`, `Patient Care Type`.

### Relevant HRMS DocTypes confirmed

`Employee` (module Setup), `Shift Assignment`, `Employee Checkin`, `Attendance`,
`Leave Application`, `Staffing Plan`, `Staffing Plan Detail`, `Leave Allocation`.

### Relevant ERPNext DocTypes confirmed

`Sales Invoice` (Accounts, 210 fields), `Item` (Stock, 107 fields).

### Bed/ward/occupancy keyword scan (important)

| Keyword | Matches | Conclusion |
|---|---|---|
| `bed` | 1 (`Notification Subscribed Document` — unrelated) | **No bed DocType** |
| `ward` | 0 | **No ward DocType** |
| `room` | 0 | **No room DocType** |
| `occupanc` | `Inpatient Occupancy` (child table) | Marley occupancy only |
| `reserv` | `Stock Reservation Entry` (unrelated) | **No bed reservation DocType** |
| `housekeep` | 0 | **No housekeeping DocType** |
| `admission` | 0 named; lifecycle lives on `Inpatient Record` | Admission handled by `Inpatient Record` |
| `transfer` | `Employee Transfer`, `Share Transfer` (unrelated) | No bed-transfer DocType |
| `discharge` | 0 named; fields on `Inpatient Record` | Discharge handled by `Inpatient Record` |
| `allocat` | Asset/Cost Center/Leave/Payment (unrelated) | No bed allocation DocType |
| `staffing` | `Staffing Plan`, `Staffing Plan Detail` (HR) | HR staffing exists; no clinical capacity snapshot |

**Conclusion:** There is no native physical bed registry, no bed reservation
model, no housekeeping model and no bed allocation engine. Marley models
*billable inpatient units* via `Healthcare Service Unit` + `Inpatient Occupancy`.

---

## 4. Important Fields — Verified

### 4.1 `Healthcare Service Unit` (the physical space / bed model)

`autoname` = `field:healthcare_service_unit_name` (tree, NestedSet: `lft`, `rgt`,
`old_parent`, `parent_healthcare_service_unit`).

| Field | Type | Notes |
|---|---|---|
| `healthcare_service_unit_name` | Data (reqd) | Used as document name |
| `is_group` | Check | Tree grouping (ward/floor) |
| `service_unit_type` | Link → Healthcare Service Unit Type | Category of unit |
| `service_unit_capacity` | Int | Number of beds in a unit (for rooms) |
| `inpatient_occupancy` | Check | Whether unit is used for inpatient stay |
| `occupancy_status` | Select `Vacant` / `Occupied` | **Authoritative occupancy flag in Marley** |
| `company` | Link → Company (reqd) | Tenant |
| `warehouse` | Link → Warehouse | Billing/inventory link |
| `allow_appointments`, `overlap_appointments` | Check | Outpatient use |
| `parent_healthcare_service_unit` | Link (self) | Tree parent |

Permissions: Nursing User (read), Healthcare Administrator (full), Physician (full).

> **Key design fact:** one `Healthcare Service Unit` row = one physical
> room/bed unit. `service_unit_capacity` exists but `Inpatient Occupancy`
> tracks one active row per occupied unit, so the allocator should treat each
> inpatient-capable leaf unit as **one allocatable resource** unless capacity
> sharing is explicitly configured.

### 4.2 `Healthcare Service Unit Type`

Fields: `disabled`, `service_unit_type` (name), `allow_appointments`,
`overlap_appointments`, `inpatient_occupancy`, `is_billable`, `item`,
`item_code`, `item_group`, `uom`, `no_of_hours`, `rate`, `description`,
`change_in_item`.

Permissions: Healthcare Administrator only.

> This is the natural anchor for **ward category → billing tariff** (General /
> ICU / Special) and for capability defaults.

### 4.3 `Inpatient Record` (admission lifecycle)

`autoname` = `naming_series:` (`HLC-INP-.YYYY.-`). Not submittable.

| Field | Type | Notes |
|---|---|---|
| `patient` | Link → Patient (reqd) | Identity |
| `patient_name`, `gender`, `blood_group`, `dob`, `mobile`, `email`, `phone` | Data/Link | Copied demographics |
| `company` | Link → Company | Tenant |
| `status` | Select: `Admission Scheduled`, `Admitted`, `Discharge Scheduled`, `Discharged`, `Cancelled` | **Admission state machine** |
| `scheduled_date` | Date (reqd) | Planned admission |
| `admitted_datetime` | Datetime | Actual admission |
| `expected_discharge` | Date | Planned discharge |
| `admission_encounter` | Link → Patient Encounter | Clinical encounter |
| `admission_practitioner` | Link → Healthcare Practitioner | |
| `medical_department` | Link → Medical Department | Specialty |
| `admission_order_for` (`admission_ordered_for`) | Date | |
| `admission_service_unit_type` | Link → Healthcare Service Unit Type | **Requested care category** |
| `admission_nursing_checklist_template`, `discharge_nursing_checklist_template` | Link | |
| `expected_length_of_stay` | Int | |
| `primary_practitioner`, `secondary_practitioner` | Link | |
| `inpatient_occupancies` | **Table → Inpatient Occupancy** | **Bed occupancy history** |
| `btn_transfer` | Button | Transfer UI action |
| `discharge_ordered_date`, `discharge_practitioner`, `discharge_encounter`, `discharge_datetime`, `discharge_instructions`, `discharge_note` | mixed | Discharge lifecycle |
| `diagnosis`, `chief_complaint`, `drug_prescription`, `lab_test_prescription`, `procedure_prescription`, `therapies` | Tables | Clinical links |

Permissions: Healthcare Administrator (full), Physician (read/write/create),
Nursing User (read/write/create) plus read-only/permlevel variants. 8 rows total.

### 4.4 `Inpatient Occupancy` (child table — authoritative bed occupancy)

`istable` = true (0 permissions; accessed via parent). Fields:

| Field | Type | Notes |
|---|---|---|
| `service_unit` | Link → Healthcare Service Unit (reqd) | The occupied unit/bed |
| `check_in` | Datetime | Occupancy start |
| `left` | Check | Whether patient has left |
| `check_out` | Datetime | Occupancy end |
| `invoiced` | Check | Billing reconciliation flag |

> This is the **single source of truth for bed occupancy** in Marley. The
> allocator must never create a parallel occupancy table; operational
> readiness (cleaning/maintenance) must be an *additional layer* keyed to the
> same `Healthcare Service Unit`.

### 4.5 `Healthcare Settings`

57 fields; none currently cover bed lifecycle, housekeeping, staffing ratios or
allocation policy. Relevant existing fields: `allow_discharge_despite_unbilled_services`,
`do_not_bill_inpatient_encounters`, `inpatient_visit_charge_item`,
`create_lab_test_on_si_submit`, `default_priority`, `default_intent`.

> Bed-allocation policy must be a **new** DocType (or custom fields), not stored
> ad hoc in settings.

### 4.6 HRMS fields verified

- **Employee** (module Setup, 108 fields): identity, status, department, designation, company.
- **Shift Assignment** (16): `employee`, `shift_type`, `shift_location`, `status`, `start_date`, `end_date`, `department`, `company`.
- **Attendance** (23): `employee`, `attendance_date`, `status`, `shift`, `in_time`, `out_time`, `working_hours`, `leave_type`, `leave_application`.
- **Leave Application** (29): `employee`, `leave_type`, `from_date`, `to_date`, `status`, `total_leave_days`, `leave_approver`.
- **Employee Checkin** (23): presence timestamps.
- **Staffing Plan / Staffing Plan Detail**: HR staffing planning (not clinical nurse-to-patient capacity).

> No field directly maps an Employee to a ward/clinical competency. That linkage
> must be added in `hospital_operations` (e.g., a Ward Assignment / Competency
> child table) or via existing HR `designation`/`department` with new custom fields.

### 4.7 Supporting Marley DocTypes

- **Medical Department** (2 fields): `department`, `patient_care_type`. 23 records seeded (Cardiology, ICU-adjacent specialties, etc.). `patient_care_type` currently empty on all rows.
- **Patient Care Type** (2 fields): `patient_care_type`, `description`. Seeded: Diagnostic, Intervention, Preventive.
- **Nursing Task** (39 fields): has `service_unit`, `inpatient_record`, `status`, `activity` — reusable for housekeeping-adjacent nursing tasks but not a bed-turnover workflow.

---

## 5. Existing Admission Lifecycle (verified field-based)

```
Patient
  └─ Patient Encounter (clinical)
        └─ Inpatient Record (status = "Admission Scheduled")
              ├─ admission_service_unit_type  (requested care category)
              ├─ inpatient_occupancies[0] = { service_unit, check_in }   → occupied
              ├─ [btn_transfer] → new Inpatient Occupancy row (check_out old, check_in new)
              ├─ status = "Discharge Scheduled" (discharge_ordered_date)
              └─ status = "Discharged" (discharge_datetime), occupancy.left = 1, check_out set
```

Notes:
- Admission, transfer and discharge are **document-state + occupancy-row**
  operations on `Inpatient Record`; there are no separate Admission/Transfer/
  Discharge DocTypes.
- `Healthcare Service Unit.occupancy_status` mirrors Vacant/Occupied.
- Validation/discharge controls live in Marley server code (not visible in
  metadata); the exact hook boundary must be confirmed in Phase 1 via source
  inspection on the bench (if shell access is granted) or by testing.

---

## 6. Existing Bed Occupancy Lifecycle

```
VACANT (Healthcare Service Unit.occupancy_status = Vacant)
   │  admission / transfer
   ▼
OCCUPIED (occupancy_status = Occupied; Inpatient Occupancy row, left=0)
   │  discharge confirmed
   ▼
Inpatient Occupancy.left = 1, check_out set  → unit Vacant
```

There is **no native concept of cleaning-required, cleaning-in-progress,
maintenance, blocked or isolation-cleaning**. Releasing a bed immediately makes
it Vacant with no turnover gate. This is the core gap the custom app must close
without duplicating occupancy.

---

## 7. Reusable Functionality

| Need | Reuse |
|---|---|
| Patient identity | `Patient` |
| Clinical context | `Patient Encounter`, `Medical Department`, `Patient Care Type` |
| Admission/transfer/discharge | `Inpatient Record` status + `Inpatient Occupancy` |
| Physical bed/room registry | `Healthcare Service Unit` (tree) |
| Ward/bed category + tariff | `Healthcare Service Unit Type` (rate, item) |
| Billing | `Sales Invoice`, `Item`, service-unit rate |
| Staffing raw data | `Employee`, `Shift Assignment`, `Attendance`, `Leave Application`, `Employee Checkin` |
| Audit scaffolding | Frappe `Version`, `Communication`, activity log, roles |
| Task tracking | `Nursing Task` (optionally) |

---

## 8. Missing Functionality (must be built in `hospital_operations`)

1. Clinical bed **capability registry** (ventilator, oxygen, isolation, cardiac monitoring, accessibility, specialty).
2. Bed **operational state** layer (ready/cleaning/maintenance/blocked) coexisting with Marley occupancy.
3. **Bed Allocation Request** with clinical requirements child table.
4. **Eligibility engine** (hard clinical constraints).
5. **Priority + cost model** and OR-Tools **Min-Cost Flow** optimiser with explainable proposals.
6. **Transaction-safe reservation** with DB-level uniqueness and row locking.
7. **Postoperative / time-window reservations**.
8. **Housekeeping** workflow and turnover verification.
9. **Staffed capacity snapshots** (nurse competency, presence, ratio).
10. **Allocation audit events** (append-only).
11. **REST APIs** under `hospital_operations.api`.
12. Required **roles**: Bed Manager, Admission Coordinator, Nursing Supervisor, Housekeeping Staff, Hospital Operations Administrator (none exist).
13. **Policy configuration** DocType (versioned rules, not hardcoded).

---

## 9. Integration Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Marley occupancy (`Inpatient Occupancy`) vs new operational state could diverge | High | Single source: derive "occupied" from Marley; operational state is an additive readiness layer keyed to `Healthcare Service Unit` |
| Admission/transfer not submittable (no DB transaction boundary) | High | Wrap claim + Marley update in one DB transaction; document boundary; add reconciliation job |
| `Administrator` token masks real permissions | High | Re-test with least-privilege service user before production |
| `Service Unit Type` currently empty; no wards/beds seeded | Medium | Phase 1 seed script (await approval before touching production) |
| No Employee↔ward/competency mapping | Medium | Add custom competency/ward assignment model |
| `Inpatient Occupancy` child-table 403 on direct query | Low | Expected: child tables have no own permissions; access via parent |
| Marley may have server-side validation not visible in metadata | Medium | Inspect installed source on bench (if shell granted) or validate via sandbox calls |
| Site is a fresh "test" company with zero patients/employees | Medium | Tests must use synthetic data; confirm this is not production |
| Frappe v15 `client.get_list` ignores `distinct`, caps at 1000/page | Low | Pagination implemented in `tools/inspect_remote.py` |
| API rate limits / 429 | Low | Client retries with `Retry-After` |

---

## 10. Proposed Extension Points

1. **Do not modify** `Patient`, `Inpatient Record`, `Healthcare Service Unit` core.
2. Add **custom fields** (via fixtures in `hospital_operations`, migration-safe) only where strictly required, e.g.:
   - `Healthcare Service Unit`: `ho_ward` (Link), `ho_is_allocatable` (Check).
3. New DocTypes in `hospital_operations` module:
   - `Bed Allocation Request`, `Bed Clinical Requirement` (child)
   - `Bed Capability`, `Bed Operational State`, `Bed Reservation`
   - `Bed Staffing Snapshot`, `Bed Allocation Policy`, `Bed Allocation Run`,
     `Bed Allocation Proposal`, `Bed Allocation Event` (append-only)
   - `Housekeeping Task`
   - Integration helper: `Ward Nurse Assignment` / competency map (HRMS bridge)
4. **Hooks** (doc_events on `Inpatient Record` / `Inpatient Occupancy`) to:
   - map admission → occupancy → operational state,
   - prevent READY before housekeeping verification,
   - emit audit events.
5. **Roles + permissions** for the five operational roles.
6. **API namespace**: `hospital_operations.api.{beds,requests,allocation,housekeeping,staffing}`.

---

## 11. Requirement → Existing Model Mapping

| Requirement | Existing DocType | Existing field/workflow | Extension needed |
|---|---|---|---|
| Patient identity | `Patient` | `patient_name`, demographics | No |
| Admission | `Inpatient Record` | `status`, `admitted_datetime`, `admission_service_unit_type` | No (wrap only) |
| Bed/room registry | `Healthcare Service Unit` | tree + `service_unit_type`, `service_unit_capacity`, `inpatient_occupancy` | Minor (custom fields) |
| Ward/bed category | `Healthcare Service Unit Type` | `service_unit_type`, `rate`, `item` | No |
| Bed occupancy | `Inpatient Occupancy` (child) | `service_unit`, `check_in`, `check_out`, `left` | No (read/derive) |
| Bed reservation | **None** | **No support** | **Yes — new DocType + DB constraints** |
| Bed operational readiness | `Healthcare Service Unit.occupancy_status` (Vacant/Occupied only) | binary only | **Yes — new state layer** |
| Housekeeping / turnover | **None** | **No support** | **Yes — new DocType** |
| Clinical eligibility | `Patient Care Type`, `Medical Department`, `Inpatient Record.diagnosis` | partial categories only | **Yes — capability + eligibility engine** |
| Admission priority | `Inpatient Record` (no priority) | none | **Yes — request priority** |
| Nurse availability | HRMS `Employee`, `Shift Assignment`, `Attendance`, `Leave Application` | shift/attendance/leave exist | **Yes — ward/competency map + capacity snapshot** |
| Staffed bed capacity | `Staffing Plan` (HR, not clinical) | planning only | **Yes — new snapshot model** |
| Billing / tariff | `Sales Invoice`, `Item`, `Healthcare Service Unit Type.rate` | existing billing | No (integration points only) |
| Transfer | `Inpatient Record` + `Inpatient Occupancy` | occupancy row rollover | Wrap + audit |
| Discharge & release | `Inpatient Record` status + `Inpatient Occupancy.left` | existing | Wrap + housekeeping gate |
| Postoperative reservation | **None** | none | **Yes — time-window reservation** |
| Audit history | Frappe `Version`/activity log | generic | **Yes — domain audit events** |
| REST APIs | Frappe REST | generic | **Yes — whitelisted methods** |
| Policy configuration | `Healthcare Settings` (no bed policy) | none | **Yes — new policy DocType** |
| Required roles | `Role` | 60 roles; none of the 5 required | **Yes — create roles** |

---

## 12. Access and Permissions Observed

- Token identity: `Administrator` (superuser). All DocType metadata reads
  returned HTTP 200.
- `Inpatient Occupancy` direct count returned **403** because it is a **child
  table** with no standalone permissions — expected, not an error.
- Existing Marley clinical DocTypes are governed by `Healthcare Administrator`,
  `Physician`, `Nursing User` (and permlevel variants).
- None of the required operational roles exist:
  `Bed Manager`, `Admission Coordinator`, `Nursing Supervisor`,
  `Housekeeping Staff`, `Hospital Operations Administrator`.
- Only Company `test` (abbr `T`) exists; `Healthcare Service Unit Type` count = 0;
  `Healthcare Service Unit` count = 1 (a group node).

### API errors encountered

| Endpoint | Status | Meaning |
|---|---|---|
| `frappe.client.get_count` on `Inpatient Occupancy` | 403 | Child table, not directly readable — expected |
| Metadata lookup for `Hospital Bed`, `Bed`, `Bed Occupancy`, `Bed Reservation`, `Housekeeping Task`, `Inpatient Record Admission/Discharge/Transfer`, `Healthcare Service Unit Booking`, `Employee Shift Assignment` | 404 | Confirmed **not installed** (recorded as absent, not failures) |

No 401, 429, 5xx or TLS errors. All core discovery calls returned HTTP 200.

---

## 13. Phase 0 Gate

Discovery is complete and read-only. **No remote data, apps or migrations were
changed.** The schema supports Phase 1 as follows:

- Reuse `Healthcare Service Unit` for the bed registry (add minimal custom fields).
- Derive occupancy from `Inpatient Occupancy`; never duplicate it.
- Build the operational readiness, capability, eligibility, reservation,
  housekeeping, staffing-capacity, audit and API layers in `hospital_operations`.
- Create the five operational roles.

**Awaiting approval before any schema-changing work (Phase 1).**
