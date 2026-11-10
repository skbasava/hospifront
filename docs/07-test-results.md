# Test Results — Phase 1 (Foundation) and Phase 2 (State & Eligibility)

**Date:** 2026-11-10
**Environment:** local repository only. No Bench site, no `bench migrate`, no
remote changes. The remote site was used only for Phase 0 read-only discovery.

## 1. Executed locally

| Check | Command | Result |
|---|---|---|
| DocType JSON syntax (12 files) | `python3` JSON load over `hospital_operations/**/*.json` | **PASS** — 12/12 parse |
| DocType structural integrity | field uniqueness, `field_order` completeness, valid fieldtypes, child/perm rules, autoname targets | **PASS** — 0 problems |
| Python syntax/import compile | `python3 -m compileall -q hospital_operations tools` | **PASS** |
| Lint (ruff, F+I rules) | `/tmp/opencode/ho-venv/bin/ruff check hospital_operations` | **PASS — 0 issues** |
| Full local unittest discovery (OR-Tools venv) | `PYTHONPATH=hospital_operations /tmp/opencode/ho-venv/bin/python -m unittest discover -s .../tests -p 'test_*.py'` | **PASS — 48 passed, 2 skipped** |
| Bed state-machine unit tests | `PYTHONPATH=hospital_operations python3 -m unittest hospital_operations.tests.test_state_machine -v` | **PASS — 7/7** |
| Stage 1 eligibility unit tests | `PYTHONPATH=hospital_operations python3 -m unittest hospital_operations.tests.test_eligibility -v` | **PASS — 22/22** |
| Cost model / priority tests | same venv runner, `test_cost_model.py` | **PASS — 9/9** |
| Optimiser tests (OR-Tools 9.15.6755) | same venv runner, `test_optimizer.py` | **PASS — 10/10** |

The two skipped modules are the Frappe integration tests (`test_doctypes`,
`test_roles`), skipped automatically because `frappe` is not importable outside
a Bench.

### State-machine cases passed

- Full happy path `READY → RESERVED → OCCUPIED → VACATED → CLEANING_REQUIRED → CLEANING_IN_PROGRESS → READY`
- `OCCUPIED → READY` rejected
- `VACATED → READY` rejected (cleaning mandatory)
- `ISOLATION_CLEANING → READY` allowed
- Maintenance/blocked paths allowed
- Unknown states rejected
- Only `READY` is allocatable

### Eligibility cases passed (Phase 2)

- Compatible ICU bed eligible; ICU↔General and General↔ICU rejected
- `Other` care level accepts any configured level
- Maintenance, blocked, vacated, both cleaning states, occupied and reserved rejected
- Non-allocatable and unknown-state beds rejected
- Ventilator mandatory requirement enforced; optional gap does not block
- Isolation accepts specific capability such as `negative_pressure`; `0` rejected
- Specialty match/mismatch, `IN` / `NOT IN`, numeric `>=`
- Accessibility true/false, room-sharing conflict, staffed-capacity exhaustion
- Case-insensitive values; structured `as_dict()` output shape

### Cost / priority cases passed (Phase 3)

- Waiting credit is lower for longer waits; future requests get no credit
- Care-level and higher-care-level components; specialty soft penalty
- Negative/non-integer weights rejected; policy JSON parsed
- Default/custom priority order parsed; malformed orders rejected

### Optimiser cases passed (Phase 3)

- Emergency beats routine for one bed; priority order is lexicographic
- Multiple requests competing for one bed; all compatible beds used
- No eligible bed produces a clear unassigned reason
- Soft cost cannot assign an ineligible pair
- Deterministic ties across repeated/reversed input; input hash stable
- Waiting-time preference; input hash changes when input changes
- Result shape contains algorithm, counts, hash and assignments

## 2. Written but not executed (no local Bench)

These require a Frappe site and must be run after an approved install:

```bash
bench --site <site> run-tests --app hospital_operations
```

| Test module | Covers |
|---|---|
| `tests/test_doctypes.py` | All 12 DocTypes exist; child-table flag; unique service-unit state; append-only event update/delete rejection |
| `tests/test_roles.py` | Five operational roles exist; Bed Manager cannot delete requests |

**Status: NOT RUN.** The local machine has the `bench` CLI but no bench
directory or site. The remote site must not be modified without approval.

## 3. Not yet applicable

Reservation/concurrency, staffing, housekeeping and API security tests belong
to Phases 4–7. No placeholder tests were marked as passing.

## 4. Known limitations at this gate

- No site installation or migration has been performed or verified. The remote
  API token cannot install an app; server/bench access is required.
- Repository, operational-readiness and run-persistence services have not run
  against a site.
- Marley's server-side admission validation remains unexercised.
- Permissions have not been tested with a least-privilege token.
- Room-sharing rules and time-window reservation conflicts are not implemented.
- Full HRMS staffed-capacity computation is Phase 5.
- The post-operative time-window locking path is not implemented yet.
- OR-Tools is installed only in a local validation venv; the Bench still needs
  `bench pip install "ortools>=9.8,<10"`.
- Runs above 1,000 requests are rejected rather than batched.
