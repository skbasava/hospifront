import unittest

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
except ImportError:
    raise unittest.SkipTest("Frappe bench required for API permission tests")

from hospital_operations.api import allocation
from hospital_operations.api import beds as beds_api
from hospital_operations.api import health as health_api
from hospital_operations.api import housekeeping as housekeeping_api
from hospital_operations.api import post_operative as post_operative_api
from hospital_operations.api import requests as requests_api
from hospital_operations.api import staffing as staffing_api
from hospital_operations.bed_allocation import housekeeping
from hospital_operations.tests import factories
from hospital_operations.utils.roles import (
    ADMISSION_COORDINATOR,
    HOUSEKEEPING_STAFF,
)


def make_api_user(*roles):
    user = frappe.get_doc(
        {
            "doctype": "User",
            "email": f"ho-api-{factories.token()}@example.invalid",
            "first_name": "HO API User",
            "send_welcome_email": 0,
        }
    ).insert(ignore_permissions=True)
    if roles:
        user.add_roles(*roles)
    return frappe.get_doc("User", user.name)


class TestAPIPermissions(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = factories.get_company()
        cls.unit_type = factories.make_service_unit_type()
        cls.ward = factories.make_ward(cls.company)
        cls.policy = factories.make_policy()
        cls.plain_user = make_api_user()
        cls.housekeeping_user = make_api_user(HOUSEKEEPING_STAFF)
        cls.admission_user = make_api_user(ADMISSION_COORDINATOR)
        cls.bed = factories.make_bed(cls.unit_type, "ICU", cls.ward, cls.company)
        cls.patient = factories.make_patient()
        cls.inpatient = factories.make_inpatient_record(
            cls.patient, cls.unit_type, cls.company
        )
        cls.request = factories.make_request(
            cls.patient, cls.inpatient, company=cls.company
        )

    def setUp(self):
        super().setUp()
        self.original_user = frappe.session.user

    def tearDown(self):
        frappe.set_user(self.original_user)
        super().tearDown()

    def test_guest_is_denied(self):
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            beds_api.list()

    def test_user_without_operational_role_is_denied(self):
        frappe.set_user(self.plain_user.name)
        with self.assertRaises(frappe.PermissionError):
            beds_api.list()

    def test_housekeeping_can_start_task_but_cannot_optimize(self):
        bed = factories.make_bed(
            self.unit_type, "ICU", self.ward, self.company, state="VACATED"
        )
        task = housekeeping.ensure_task(bed.name)
        frappe.set_user(self.housekeeping_user.name)

        result = housekeeping_api.start(task=task.name)
        self.assertEqual(result["status"], "IN_PROGRESS")

        with self.assertRaises(frappe.PermissionError):
            allocation.optimize(request_names=[self.request.name])

    def test_admission_coordinator_cannot_start_housekeeping(self):
        bed = factories.make_bed(
            self.unit_type, "ICU", self.ward, self.company, state="VACATED"
        )
        task = housekeeping.ensure_task(bed.name)
        frappe.set_user(self.admission_user.name)

        with self.assertRaises(frappe.PermissionError):
            housekeeping_api.start(task=task.name)

    def test_admission_coordinator_cannot_preempt_post_operative_hold(self):
        frappe.set_user(self.admission_user.name)

        with self.assertRaises(frappe.PermissionError):
            post_operative_api.preempt(
                reservation="BRES-NOT-A-REAL-RESERVATION",
                emergency_request=self.request.name,
                clinical_approval_reference="APPROVAL-1",
            )

    def test_requests_create_rejects_missing_patient(self):
        with self.assertRaises(frappe.ValidationError):
            requests_api.create(patient=None, care_level="ICU")

    def test_staffing_capacity_requires_role(self):
        frappe.set_user(self.admission_user.name)
        with self.assertRaises(frappe.PermissionError):
            staffing_api.capacity(ward=self.ward.name)

    def test_invalid_payload_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            beds_api.list(limit=0)
        with self.assertRaises(frappe.ValidationError):
            requests_api.list(start=-1)
        with self.assertRaises(frappe.ValidationError):
            requests_api.create(
                patient=self.patient.name,
                inpatient_record=self.inpatient.name,
                requirements="not-json",
            )

    def test_mutation_and_read_methods_are_explicit(self):
        methods = frappe.allowed_http_methods_for_whitelisted_func
        for func in (
            beds_api.list,
            beds_api.availability,
            allocation.get_result,
            health_api.status,
            requests_api.list,
            staffing_api.capacity,
        ):
            self.assertEqual(methods[func], ["GET"])
        for func in (
            requests_api.create,
            allocation.optimize,
            allocation.confirm,
            allocation.cancel,
            allocation.transfer,
            allocation.discharge,
            housekeeping_api.start,
            housekeeping_api.complete,
            housekeeping_api.verify,
            housekeeping_api.cancel,
            post_operative_api.create,
            post_operative_api.reschedule,
            post_operative_api.cancel,
            post_operative_api.confirm,
            post_operative_api.preempt,
        ):
            self.assertEqual(methods[func], ["POST"])
