"""Synthetic fixtures for reservation and ADT tests.

Every factory returns committed-independent Frappe documents. Tests are expected
to run inside a FrappeTestCase so the class cleanup rolls the rows back.
"""

import json

import frappe
from frappe.utils import now_datetime, today

from hospital_operations.bed_allocation import operational
from hospital_operations.bed_allocation.run import create_allocation_run


def token():
    return frappe.generate_hash(length=8)


def get_company():
    company = frappe.defaults.get_defaults().get("company") or frappe.db.get_value(
        "Company", {}, "name"
    )
    if not company:
        frappe.throw("A Company is required for hospital operations tests.")
    return company


def get_root_service_unit():
    names = frappe.get_all(
        "Healthcare Service Unit",
        filters={"is_group": 1},
        pluck="name",
        order_by="lft asc",
        limit_page_length=1,
    )
    if not names:
        frappe.throw("A root Healthcare Service Unit is required for tests.")
    return names[0]


def make_service_unit_type(care_level="ICU"):
    name = f"HO Test {care_level} {token()}"
    return frappe.get_doc(
        {
            "doctype": "Healthcare Service Unit Type",
            "service_unit_type": name,
            "inpatient_occupancy": 1,
            "allow_appointments": 0,
            "is_billable": 0,
        }
    ).insert(ignore_permissions=True)


def make_billable_service_unit_type(care_level="ICU", rate=100):
    return frappe.get_doc(
        {
            "doctype": "Healthcare Service Unit Type",
            "service_unit_type": f"HO Billable {care_level} {token()}",
            "inpatient_occupancy": 1,
            "allow_appointments": 0,
            "is_billable": 1,
            "item_code": f"HO-SVC-{token()}",
            "item_group": "Services",
            "uom": "Nos",
            "rate": rate,
        }
    ).insert(ignore_permissions=True)


def make_ward(company=None, parent=None):
    company = company or get_company()
    return frappe.get_doc(
        {
            "doctype": "Healthcare Service Unit",
            "healthcare_service_unit_name": f"HO Ward {token()}",
            "is_group": 1,
            "company": company,
            "parent_healthcare_service_unit": parent or get_root_service_unit(),
        }
    ).insert(ignore_permissions=True)


def make_bed(
    service_unit_type,
    care_level="ICU",
    ward=None,
    company=None,
    allocatable=1,
    state="READY",
):
    company = company or get_company()
    ward = ward or make_ward(company)
    bed = frappe.get_doc(
        {
            "doctype": "Healthcare Service Unit",
            "healthcare_service_unit_name": f"HO Bed {token()}",
            "company": company,
            "parent_healthcare_service_unit": ward.name,
            "service_unit_type": service_unit_type.name,
            "ho_ward": ward.name,
            "ho_is_allocatable": allocatable,
            "ho_care_level": care_level,
        }
    ).insert(ignore_permissions=True)
    if state:
        operational.ensure_state(bed.name, initial_state=state)
    return bed


def make_patient():
    return frappe.get_doc(
        {
            "doctype": "Patient",
            "first_name": f"HO Test {token()}",
            "sex": "Other",
        }
    ).insert(ignore_permissions=True)


def make_inpatient_record(patient, service_unit_type, company=None):
    return frappe.get_doc(
        {
            "doctype": "Inpatient Record",
            "patient": patient.name,
            "company": company or get_company(),
            "scheduled_date": today(),
            "admission_service_unit_type": service_unit_type.name,
        }
    ).insert(ignore_permissions=True)


def make_policy(expiry_minutes=120, staffing=None, eligibility=None):
    rules = dict(eligibility or {})
    if staffing is not None:
        rules["staffing"] = staffing
    return frappe.get_doc(
        {
            "doctype": "Bed Allocation Policy",
            "policy_name": f"HO Test Policy {token()}",
            "version": "1",
            "enabled": 1,
            "reservation_expiry": expiry_minutes,
            "clinical_priority_rules": json.dumps(
                {"order": ["Emergency", "Urgent", "Routine"]}
            ),
            "eligibility_rules": json.dumps(rules) if rules else None,
        }
    ).insert(ignore_permissions=True)


def make_employee(company=None, status="Active", relieving_date=None):
    return frappe.get_doc(
        {
            "doctype": "Employee",
            "naming_series": "HR-EMP-",
            "first_name": f"HO Nurse {token()}",
            "gender": "Other",
            "date_of_birth": "1990-01-01",
            "date_of_joining": "2020-01-01",
            "company": company or get_company(),
            "status": status,
            "relieving_date": relieving_date,
        }
    ).insert(ignore_permissions=True)


def make_shift_type(start_time="09:00:00", end_time="17:00:00"):
    return frappe.get_doc(
        {
            "doctype": "Shift Type",
            "name": f"HO Shift {token()}",
            "start_time": start_time,
            "end_time": end_time,
            "begin_check_in_before_shift_start_time": 0,
            "allow_check_out_after_shift_end_time": 0,
        }
    ).insert(ignore_permissions=True)


def make_shift_assignment(
    employee,
    shift_type,
    start_date=None,
    end_date=None,
    status="Active",
    submit=True,
):
    start_date = start_date or today()
    doc = frappe.get_doc(
        {
            "doctype": "Shift Assignment",
            "employee": employee.name,
            "company": employee.company,
            "shift_type": shift_type.name,
            "start_date": start_date,
            "end_date": end_date or start_date,
            "status": status,
        }
    ).insert(ignore_permissions=True)
    if submit and doc.meta.is_submittable:
        doc.submit()
    return doc


def make_attendance(
    employee,
    status="Present",
    attendance_date=None,
    submit=True,
):
    attendance_date = attendance_date or today()
    existing = frappe.get_all(
        "Attendance",
        filters={
            "employee": employee.name,
            "attendance_date": attendance_date,
            "docstatus": ("<", 2),
        },
        fields=["name", "docstatus", "status"],
        order_by="modified desc",
        limit_page_length=1,
    )
    if existing:
        doc = frappe.get_doc("Attendance", existing[0].name)
        if submit and doc.docstatus == 0 and doc.meta.is_submittable:
            doc.submit()
        if doc.status != status:
            doc.db_set("status", status)
        return doc
    doc = frappe.get_doc(
        {
            "doctype": "Attendance",
            "naming_series": "HR-ATT-.YYYY.-",
            "employee": employee.name,
            "company": employee.company,
            "status": status,
            "attendance_date": attendance_date,
        }
    ).insert(ignore_permissions=True)
    if submit and doc.meta.is_submittable:
        doc.submit()
    return doc


def make_leave_type():
    return frappe.get_doc(
        {
            "doctype": "Leave Type",
            "leave_type_name": f"HO Sick {token()}",
        }
    ).insert(ignore_permissions=True)


def make_leave_application(
    employee,
    from_date=None,
    to_date=None,
    leave_type=None,
    status="Approved",
    submit=True,
):
    from_date = from_date or today()
    leave_type = leave_type or make_leave_type()
    doc = frappe.get_doc(
        {
            "doctype": "Leave Application",
            "naming_series": "HR-LAP-.YYYY.-",
            "employee": employee.name,
            "company": employee.company,
            "leave_type": leave_type.name,
            "from_date": from_date,
            "to_date": to_date or from_date,
            "posting_date": today(),
            "status": status,
            "docstatus": 1 if submit else 0,
        }
    )
    doc.db_insert()
    return doc


def make_ward_nurse_assignment(
    employee,
    ward,
    competency="ICU",
    valid_from=None,
    valid_to=None,
    active=1,
):
    return frappe.get_doc(
        {
            "doctype": "Ward Nurse Assignment",
            "employee": employee.name,
            "ward": ward.name,
            "competency": competency,
            "valid_from": valid_from or today(),
            "valid_to": valid_to,
            "active": active,
        }
    ).insert(ignore_permissions=True)


def make_checkin(employee, log_type="IN", time=None, shift=None):
    return frappe.get_doc(
        {
            "doctype": "Employee Checkin",
            "employee": employee.name,
            "log_type": log_type,
            "time": time or now_datetime(),
            "shift": shift,
        }
    ).insert(ignore_permissions=True)


def make_request(
    patient,
    inpatient_record,
    care_level="ICU",
    priority="Routine",
    source="Direct Admission",
    company=None,
):
    return frappe.get_doc(
        {
            "doctype": "Bed Allocation Request",
            "patient": patient.name,
            "patient_name": patient.patient_name,
            "inpatient_record": inpatient_record.name,
            "source": source,
            "priority": priority,
            "care_level": care_level,
            "company": company or get_company(),
            "requested_at": now_datetime(),
            "requested_by": "Administrator",
            "status": "PENDING",
            "idempotency_key": f"request:{token()}",
        }
    ).insert(ignore_permissions=True)


def make_allocation(bed, request, policy):
    run = create_allocation_run(
        [request.name],
        policy_name=policy.name,
        bed_names=[bed.name],
    )
    proposals = frappe.get_all(
        "Bed Allocation Proposal",
        filters={"allocation_run": run.name, "allocation_request": request.name},
        pluck="name",
    )
    if not proposals:
        frappe.throw("Expected an allocation proposal for the test request.")
    return run, frappe.get_doc("Bed Allocation Proposal", proposals[0])


def make_proposal_for_bed(bed, request, run, priority="Routine"):
    state = operational.get_state(bed.name)
    return frappe.get_doc(
        {
            "doctype": "Bed Allocation Proposal",
            "allocation_run": run.name,
            "allocation_request": request.name,
            "proposed_bed": bed.name,
            "status": "PROPOSED",
            "cost": 0,
            "priority": priority,
            "bed_state_version": state.version if state else 0,
            "eligibility_summary": "{}",
            "explanation": "synthetic test proposal",
        }
    ).insert(ignore_permissions=True)


def make_postop_reservation(
    request,
    bed,
    inpatient_record,
    start_at=None,
    expires_at=None,
    key=None,
    status="ACTIVE",
):
    start_at = start_at or now_datetime()
    expires_at = expires_at or None
    if expires_at is None:
        from frappe.utils import add_to_date

        expires_at = add_to_date(start_at, hours=4)
    return frappe.get_doc(
        {
            "doctype": "Bed Reservation",
            "allocation_request": request.name,
            "healthcare_service_unit": bed.name,
            "inpatient_record": inpatient_record.name,
            "reservation_type": "POST_OPERATIVE",
            "status": status,
            "start_at": start_at,
            "expires_at": expires_at,
            "idempotency_key": key or f"postop:{token()}",
            "created_by": "Administrator",
        }
    ).insert(ignore_permissions=True)


def make_active_reservation(request, bed, inpatient_record, key=None, expired=False):
    start = now_datetime()
    expires = start
    if expired:
        from frappe.utils import add_to_date

        start = add_to_date(start, minutes=-30)
        expires = add_to_date(start, minutes=10)
    else:
        from frappe.utils import add_to_date

        expires = add_to_date(start, minutes=120)
    return frappe.get_doc(
        {
            "doctype": "Bed Reservation",
            "allocation_request": request.name,
            "healthcare_service_unit": bed.name,
            "inpatient_record": inpatient_record.name,
            "reservation_type": "IMMEDIATE",
            "status": "ACTIVE",
            "start_at": start,
            "expires_at": expires,
            "idempotency_key": key or f"reservation:{token()}",
            "created_by": "Administrator",
        }
    ).insert(ignore_permissions=True)
