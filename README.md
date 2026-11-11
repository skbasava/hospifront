# Hospital Operations

Operational Bed Allocation System for an existing Frappe v15 site running
Frappe Health (Marley), ERPNext and HRMS.

This branch (`bff_development`) contains the Frappe backend app at the
repository root. The React frontend is maintained on the `master` and
`development` branches.

This app is backend-only. It adds an operational layer on top of Marley's
existing `Inpatient Record` / `Inpatient Occupancy` model; it never replaces
the authoritative patient, admission or occupancy records.

## Status

Phases 1–7 (Foundation, State & Eligibility, Allocation Engine, Reservation &
ADT, HRMS & Housekeeping, OT & ERPNext, APIs & Validation) are implemented. The
app is installed and validated on the local Bench site `hospital.localhost`;
the remote site has not been modified and no migration has been run from this
repository. The full Bench suite passes 150/150, including API contracts,
permission-matrix, concurrency and benchmark validation. See `docs/` for the
analysis, implementation plan, API contracts (`docs/05-api-contracts.md`),
concurrency design, test reports and the
[final implementation report](docs/08-final-implementation-report.md).

## Installation (requires explicit approval)

```bash
bench get-app --branch bff_development \
  https://github.com/skbasava/hospifront.git
bench --site <site> install-app hospital_operations
bench --site <site> migrate
```

The repository root is the Frappe app (`pyproject.toml` plus the
`hospital_operations` Python package), so `bench get-app` installs it
directly. The React frontend files in the same repository are ignored by
Bench.

`install-app` / `migrate` create the custom DocTypes, the five operational
roles, additive custom fields on `Healthcare Service Unit`, database indexes and
the active-reservation uniqueness constraint. They do not seed patients, beds
or employees.

Optional allocation dependency (Phase 3+):

```bash
bench pip install "ortools>=9.8,<10"
```

## Local/remote discovery switch (read-only)

Set `FRAPPE_TARGET` to `local` or `remote` (default: `remote`). The
`--target` flag overrides the environment variable.

Remote:

```bash
export FRAPPE_TARGET=remote
export FRAPPE_REMOTE_BASE_URL=https://frappe.abhidigitech.com
export FRAPPE_REMOTE_API_KEY=...
export FRAPPE_REMOTE_API_SECRET=...
python3 tools/inspect_remote.py
```

Local bench:

```bash
export FRAPPE_TARGET=local
export FRAPPE_LOCAL_BASE_URL=http://127.0.0.1:8000
export FRAPPE_LOCAL_API_KEY=...
export FRAPPE_LOCAL_API_SECRET=...
python3 tools/inspect_remote.py --target local
```

Remote discovery writes `artifacts/frappe_schema_report.json`; local
discovery writes `artifacts/frappe_schema_report_local.json`. Use
`--output PATH` to override. The legacy `FRAPPE_BASE_URL`,
`FRAPPE_API_KEY` and `FRAPPE_API_SECRET` variables still work for the
remote target.

Secrets are read from the environment only and are never logged.
