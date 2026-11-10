# Hospital Operations

Operational Bed Allocation System for an existing Frappe v15 site running
Frappe Health (Marley), ERPNext and HRMS.

This app is backend-only. It adds an operational layer on top of Marley's
existing `Inpatient Record` / `Inpatient Occupancy` model; it never replaces
the authoritative patient, admission or occupancy records.

## Status

Phases 1–3 (Foundation, State & Eligibility, Allocation Engine) scaffolding.
No site has been modified and no migration has been run from this repository.
See `docs/` for the approved analysis, implementation plan and phase reports.

## Installation (requires explicit approval)

```bash
bench get-app /path/to/hospital_operations
bench --site <site> install-app hospital_operations
bench --site <site> migrate
```

`install-app` / `migrate` create the custom DocTypes, the five operational
roles, additive custom fields on `Healthcare Service Unit`, database indexes and
the active-reservation uniqueness constraint. They do not seed patients, beds
or employees.

Optional allocation dependency (Phase 3+):

```bash
bench pip install "ortools>=9.8,<10"
```

## Remote discovery (read-only)

```bash
export FRAPPE_BASE_URL=https://frappe.abhidigitech.com
export FRAPPE_API_KEY=...
export FRAPPE_API_SECRET=...
python3 tools/inspect_remote.py
```

Secrets are read from the environment only and are never logged.
