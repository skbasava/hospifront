"""Read-only schema discovery for a local or remote Frappe site.

Select the target with FRAPPE_TARGET=local|remote or --target.
Produce artifacts/frappe_schema_report.json for remote and
artifacts/frappe_schema_report_local.json for local, unless --output is given.

Run:
    python3 tools/inspect_remote.py --target remote
    FRAPPE_TARGET=local python3 tools/inspect_remote.py

No POST/PUT/DELETE requests are made. Only GET endpoints are used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from remote_client import TARGETS, FrappeClient, FrappeClientError  # noqa: E402

ARTIFACT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts")
REMOTE_ARTIFACT_PATH = os.path.join(ARTIFACT_DIR, "frappe_schema_report.json")
LOCAL_ARTIFACT_PATH = os.path.join(ARTIFACT_DIR, "frappe_schema_report_local.json")

KEY_DOCTYPES = [
    "Patient",
    "Patient Encounter",
    "Inpatient Record",
    "Inpatient Occupancy",
    "Healthcare Service Unit",
    "Healthcare Service Unit Type",
    "Healthcare Settings",
    "Medical Department",
    "Patient Appointment",
    "Employee",
    "Shift Assignment",
    "Employee Checkin",
    "Attendance",
    "Leave Application",
    "Sales Invoice",
    "Item",
    "Hospital Bed",
    "Bed",
    "Nursing Task",
    "Patient Care Type",
]

# DocTypes that may or may not exist depending on version; existence is verified.
OPTIONAL_DOCTYPES = [
    "Inpatient Record Admission",
    "Inpatient Record Discharge",
    "Inpatient Record Transfer",
    "Healthcare Service Unit Booking",
    "Bed Occupancy",
    "Bed Reservation",
    "Housekeeping Task",
    "Employee Shift Assignment",
]


def safe(label, fn):
    try:
        return fn(), None
    except FrappeClientError as exc:
        return None, {"label": label, "status": exc.status, "message": exc.message, "detail": _trim(exc.detail)}


def _trim(detail):
    if isinstance(detail, dict):
        exc = detail.get("exception") or detail.get("exc")
        return (exc or detail.get("message") or "")[:400]
    return str(detail)[:400] if detail else None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Frappe schema discovery")
    parser.add_argument(
        "--target",
        choices=TARGETS,
        default=None,
        help="Override FRAPPE_TARGET (local or remote)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Report path; defaults to a target-specific file under artifacts/",
    )
    return parser.parse_args(argv)


def _artifact_path(target: str, output: str | None = None) -> str:
    if output:
        return os.path.abspath(output)
    return REMOTE_ARTIFACT_PATH if target == "remote" else LOCAL_ARTIFACT_PATH


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    errors: list[dict] = []
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
    }

    try:
        client = FrappeClient(target=args.target)
    except FrappeClientError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    report["target"] = client.config.target
    report["client_config"] = client.config.redacted()
    artifact_path = _artifact_path(client.config.target, args.output)
    site_label = f" ({client.config.site})" if client.config.site else ""
    print(f"Target: {client.config.target}{site_label} {client.config.base_url}")

    # 1. Authentication
    user, err = safe("get_logged_user", client.authenticate)
    if err:
        errors.append(err)
        print(f"AUTH FAILED: {err['message']} (status {err['status']})", file=sys.stderr)
        _write(report, errors, artifact_path)
        return 3
    report["authenticated_user"] = user
    print(f"Authenticated as: {user}")

    # 2. Versions
    versions, err = safe("get_versions", client.get_versions)
    if err:
        errors.append(err)
        versions = {}
    report["versions"] = versions
    print("Installed apps:")
    for app, meta in versions.items():
        print(f"  - {app}: {meta.get('version')} (branch {meta.get('branch')})")

    # 3. All DocTypes with module (paginated to avoid timeouts)
    report["doctype_summary"] = {}
    module_counter: Counter[str] = Counter()
    all_doctypes: list[dict] = []
    try:
        offset = 0
        page_size = 1000
        while True:
            chunk = client.get_list(
                "DocType",
                fields=["name", "module", "istable", "issingle", "custom"],
                limit=page_size,
                order_by="name asc",
                start=offset,
            )
            if not chunk:
                break
            all_doctypes.extend(chunk)
            offset += len(chunk)
            if len(chunk) < page_size:
                break
    except FrappeClientError as exc:
        errors.append({"label": "doctype_list", "status": exc.status, "message": exc.message, "detail": _trim(exc.detail)})

    for row in all_doctypes:
        module_counter[row.get("module") or "UNKNOWN"] += 1
    report["doctype_summary"] = {
        "total_doctypes": len(all_doctypes),
        "modules": dict(sorted(module_counter.items())),
        "healthcare_doctypes": sorted(
            r["name"] for r in all_doctypes if r.get("module") == "Healthcare"
        ),
        "hr_doctypes": sorted(r["name"] for r in all_doctypes if r.get("module") == "HR"),
        "payroll_doctypes": sorted(r["name"] for r in all_doctypes if r.get("module") == "Payroll"),
        "erpnext_module_counts": {
            m: c
            for m, c in sorted(module_counter.items())
            if m
            in {
                "Accounts",
                "Stock",
                "Selling",
                "Buying",
                "Manufacturing",
                "Assets",
                "Projects",
                "Quality Management",
                "Setup",
                "Healthcare",
                "HR",
                "Payroll",
            }
        },
    }
    print(f"Total DocTypes visible: {len(all_doctypes)}")
    print(f"Healthcare DocTypes: {len(report['doctype_summary']['healthcare_doctypes'])}")
    print(f"HR DocTypes: {len(report['doctype_summary']['hr_doctypes'])}")

    # 4. Metadata for key doctypes
    report["doctypes"] = {}
    for dt in KEY_DOCTYPES + OPTIONAL_DOCTYPES:
        meta, err = safe(dt, lambda d=dt: client.get_doctype_metadata(d))
        if err:
            # 404 = not installed; record as absent, not a hard error
            if err["status"] == 404:
                report["doctypes"][dt] = {"exists": False, "status": 404}
                print(f"  [absent] {dt} (HTTP 404)")
                continue
            errors.append(err)
            report["doctypes"][dt] = {"exists": None, "error": err}
            print(f"  [error]  {dt}: {err['message']} ({err['status']})")
            continue

        fields = [
            {
                "fieldname": f.get("fieldname"),
                "label": f.get("label"),
                "fieldtype": f.get("fieldtype"),
                "options": f.get("options"),
                "reqd": bool(f.get("reqd")),
                "read_only": bool(f.get("read_only")),
                "in_list_view": bool(f.get("in_list_view")),
            }
            for f in meta.get("fields", [])
        ]
        permissions = [
            {
                "role": p.get("role"),
                "read": bool(p.get("read")),
                "write": bool(p.get("write")),
                "create": bool(p.get("create")),
                "delete": bool(p.get("delete")),
                "submit": bool(p.get("submit")),
                "cancel": bool(p.get("cancel")),
                "amend": bool(p.get("amend")),
                "if_owner": bool(p.get("if_owner")),
                "permlevel": p.get("permlevel"),
            }
            for p in meta.get("permissions", [])
        ]
        report["doctypes"][dt] = {
            "exists": True,
            "module": meta.get("module"),
            "istable": meta.get("istable"),
            "issingle": meta.get("issingle"),
            "issubmittable": meta.get("issubmittable"),
            "autoname": meta.get("autoname"),
            "field_count": len(fields),
            "fields": fields,
            "permissions": permissions,
        }
        print(f"  [ok]     {dt}: {len(fields)} fields, {len(permissions)} permission rows (module {meta.get('module')})")

    report["errors"] = errors
    _write(report, errors, artifact_path)
    print(f"\nReport written to {artifact_path}")
    if errors:
        print(f"Encountered {len(errors)} API error(s):")
        for e in errors:
            print(f"  - {e['label']}: [{e['status']}] {e['message']}")
    else:
        print("No API errors encountered.")
    return 0


def _write(report: dict, errors: list[dict], path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    report["errors"] = errors
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=False)


if __name__ == "__main__":
    raise SystemExit(main())
