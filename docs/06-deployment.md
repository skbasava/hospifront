# Deployment and Rollback (Phase 1)

**Important:** the REST API token used for discovery is an `Administrator`
token. It can read/write records but **cannot install a Frappe app**. App
installation requires shell access to the Frappe Bench that serves
`frappe.abhidigitech.com`.

This document is for the operator who has that access. Do not run these
commands from this repository, and do not run them without an approved
maintenance window.

---

## 1. Preconditions

- Bench serving the target site, with `frappe`, `erpnext`, `hrms` and
  `healthcare` installed (verified remotely: 15.126.0 / 15.124.5 / 15.64.8 /
  15.2.5).
- The `hospital_operations` app directory available on the server.
- A current backup and written rollback approval.
- No seeding of patients, beds or employees as part of this deployment.

## 2. Backup

```bash
cd /path/to/frappe-bench
bench --site frappe.abhidigitech.com backup --with-files
```

Record the backup paths. Do not continue if the backup fails.

## 3. Install

```bash
cd /path/to/frappe-bench

# Make the app available to the bench (path or git URL)
bench get-app /path/to/hospital_operations

# Install on the site (runs before_install, DocType sync, after_install)
bench --site frappe.abhidigitech.com install-app hospital_operations

# Apply remaining patches/indexes (after_migrate is idempotent)
bench --site frappe.abhidigitech.com migrate

# Restart workers/web if your bench layout requires it
bench restart
```

What the install creates:

- 12 custom DocTypes (Phase 1 schema).
- Five operational roles.
- Additive `ho_ward`, `ho_is_allocatable`, `ho_care_level` fields on
  `Healthcare Service Unit`.
- Indexes and the unique active-immediate-claim constraint on
  `Bed Reservation`.
- No patients, admissions, beds, employees, invoices or occupancy records.

## 4. Validate

```bash
bench --site frappe.abhidigitech.com execute \
  hospital_operations.install.validate_installation

bench --site frappe.abhidigitech.com run-tests --app hospital_operations
```

`validate_installation` returns a dictionary of checks. `ok: true` means all
DocTypes, roles, custom fields, indexes and the uniqueness constraint are
present and no duplicate active claims exist.

Health probe after login:

```text
GET /api/method/hospital_operations.api.health.status
```

It requires authentication and returns no patient data or secrets.

## 5. Seeding (NOT part of this deployment)

Beds, ward categories, capabilities and policies must be seeded only after a
separate approval. Suggested initial capacity (configuration, not hardcoded):

| Ward category | Beds |
|---|---:|
| General ward | 9 |
| ICU | 12 |
| Special ward | 39 |
| **Total** | **60** |

Seed procedure (later phase):

1. Create/verify `Healthcare Service Unit Type` records with
   `inpatient_occupancy` and billing links.
2. Create ward-level group service units.
3. Create bed-level service units with `ho_ward`, `ho_is_allocatable=1` and
   `ho_care_level`.
4. Create `Bed Operational State` rows (use
   `initialize_allocatable_states`).
5. Create `Bed Capability` records from the approved clinical equipment list.
6. Create an enabled `Bed Allocation Policy` with hospital-approved JSON rules.

## 6. Rollback

```bash
cd /path/to/frappe-bench
bench --site frappe.abhidigitech.com uninstall-app hospital_operations
bench remove-app hospital_operations
```

- `before_uninstall` removes the additive custom fields from
  `Healthcare Service Unit`. Back up first; bed configuration in those fields
  is discarded.
- The five roles are intentionally **not** deleted automatically; remove them
  manually only after confirming no other app/user depends on them.
- DocTypes and their tables are dropped by Frappe's uninstall.
- If rollback must restore data, use the backup taken in step 2 with the
  bench's restore procedure.

## 7. Security Notes

- Never paste API secrets into commands, logs or tickets.
- Keep TLS verification enabled.
- Use a least-privilege service user for API testing once available; the
  current discovery token is `Administrator`.
- Do not grant the five operational roles `System Manager`.
- Do not run `bench migrate` automatically from CI against production.

## 8. Current Status

Phase 1 has **not** been installed. Remote read-only verification on
2026-11-10 confirmed the DocTypes and roles are absent. Phase 2 code is
scaffolded locally; it cannot be exercised against a site until Phase 1 is
installed.
