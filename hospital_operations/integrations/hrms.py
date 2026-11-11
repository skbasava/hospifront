"""Read-only HRMS adapters for clinical staffing capacity.

Presence is resolved from submitted attendance records by default. Shift
schedules only define availability windows; they are never treated as proof
that a nurse is present. Approved leave always excludes a nurse.
"""

from datetime import datetime, time, timedelta

import frappe
from frappe.utils import get_datetime, getdate

from hospital_operations.bed_allocation.staffing import (
    NurseSignal,
    resolve_presence,
    shift_window,
)


def build_nurse_signals(wards, at, rules):
    """Return ``ward -> [NurseSignal]`` for the evaluation instant."""

    wards = sorted({ward for ward in wards if ward})
    if not wards:
        return {}
    at = get_datetime(at)
    evaluation_date = getdate(at)

    assignments = _load_ward_assignments(wards, evaluation_date)
    employees = sorted({row.employee for row in assignments})
    if not employees:
        return {ward: [] for ward in wards}

    active = _active_employees(employees, evaluation_date)
    shift_rows = _load_shift_assignments(employees, evaluation_date)
    shift_types = _load_shift_types(
        {
            row.shift_type
            for rows in shift_rows.values()
            for row in rows
            if row.shift_type
        }
    )
    dates = [evaluation_date, evaluation_date - timedelta(days=1)]
    attendance = _load_attendance(employees, dates)
    on_leave = _load_approved_leave(employees, dates)
    checkins = _load_last_checkin(employees, dates)

    assignments_by_ward = {ward: {} for ward in wards}
    for row in assignments:
        competencies = assignments_by_ward[row.ward].setdefault(
            row.employee, set()
        )
        competencies.add((row.competency or "GENERAL").strip())

    signals_by_ward = {}
    for ward in wards:
        signals = []
        for employee, competencies in sorted(
            assignments_by_ward.get(ward, {}).items()
        ):
            on_shift, shift_type, shift_date = resolve_shift(
                shift_rows.get(employee, []), shift_types, at
            )
            attendance_status = attendance.get(
                (employee, shift_date or evaluation_date)
            )
            if rules.presence_source == "checkin":
                present = checkins.get(employee) == "IN"
            else:
                present = resolve_presence(rules, attendance_status)
            signals.append(
                NurseSignal(
                    employee=employee,
                    ward=ward,
                    competencies=frozenset(competencies),
                    active=active.get(employee, False),
                    on_leave=employee in on_leave,
                    on_shift=on_shift,
                    present=present,
                    attendance_status=attendance_status,
                    shift_type=shift_type,
                )
            )
        signals_by_ward[ward] = signals
    return signals_by_ward


def resolve_shift(rows, shift_types, at):
    """Return ``(on_shift, shift_type, shift_start_date)`` for an employee."""

    at = get_datetime(at)
    for row in sorted(rows, key=lambda value: (value.shift_type or "", value.name)):
        shift_type = shift_types.get(row.shift_type)
        if not shift_type:
            continue
        for offset in (0, -1):
            on_date = at.date() + timedelta(days=offset)
            if row.start_date and getdate(row.start_date) > on_date:
                continue
            if row.end_date and getdate(row.end_date) < on_date:
                continue
            start, end = shift_window(
                shift_type.start_time, shift_type.end_time, on_date
            )
            if start <= at < end:
                return True, row.shift_type, on_date
    return False, None, None


def _load_ward_assignments(wards, on_date):
    rows = frappe.get_all(
        "Ward Nurse Assignment",
        filters={
            "ward": ("in", list(wards)),
            "active": 1,
            "valid_from": ("<=", on_date),
            "docstatus": ("<", 2),
        },
        fields=["employee", "ward", "competency", "valid_from", "valid_to"],
        limit_page_length=0,
    )
    return [
        row
        for row in rows
        if not row.valid_to or getdate(row.valid_to) >= on_date
    ]


def _active_employees(employees, on_date):
    rows = frappe.get_all(
        "Employee",
        filters={"name": ("in", employees)},
        fields=["name", "status", "relieving_date"],
        limit_page_length=0,
    )
    return {
        row.name: bool(
            row.status == "Active"
            and (
                not row.relieving_date
                or getdate(row.relieving_date) > on_date
            )
        )
        for row in rows
    }


def _load_shift_assignments(employees, on_date):
    rows = frappe.get_all(
        "Shift Assignment",
        filters={
            "employee": ("in", employees),
            "start_date": ("<=", on_date),
            "status": "Active",
            "docstatus": 1,
        },
        fields=[
            "name",
            "employee",
            "shift_type",
            "start_date",
            "end_date",
        ],
        limit_page_length=0,
    )
    grouped = {}
    for row in rows:
        grouped.setdefault(row.employee, []).append(row)
    return grouped


def _load_shift_types(names):
    names = sorted({name for name in names if name})
    if not names:
        return {}
    rows = frappe.get_all(
        "Shift Type",
        filters={"name": ("in", names)},
        fields=["name", "start_time", "end_time"],
        limit_page_length=0,
    )
    return {row.name: row for row in rows}


def _load_attendance(employees, dates):
    rows = frappe.get_all(
        "Attendance",
        filters={
            "employee": ("in", employees),
            "attendance_date": ("in", list(dates)),
            "docstatus": 1,
        },
        fields=["employee", "attendance_date", "status", "modified"],
        order_by="modified desc",
        limit_page_length=0,
    )
    result = {}
    for row in rows:
        result.setdefault(
            (row.employee, getdate(row.attendance_date)), row.status
        )
    return result


def _load_approved_leave(employees, dates):
    rows = frappe.get_all(
        "Leave Application",
        filters={
            "employee": ("in", employees),
            "from_date": ("<=", max(dates)),
            "to_date": (">=", min(dates)),
            "status": "Approved",
            "docstatus": 1,
        },
        fields=["employee", "from_date", "to_date"],
        limit_page_length=0,
    )
    on_leave = set()
    for row in rows:
        for on_date in dates:
            if getdate(row.from_date) <= on_date <= getdate(row.to_date):
                on_leave.add(row.employee)
                break
    return on_leave


def _load_last_checkin(employees, dates):
    start = datetime.combine(min(dates), time.min)
    end = datetime.combine(max(dates), time.max)
    rows = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": ("in", employees),
            "time": ("between", [start, end]),
            "docstatus": ("<", 2),
        },
        fields=["employee", "time", "log_type"],
        order_by="time asc",
        limit_page_length=0,
    )
    last = {}
    for row in rows:
        last[row.employee] = row.log_type
    return last
