import unittest

try:
    import frappe
    from frappe.tests.utils import FrappeTestCase
    from frappe.utils import add_to_date, now_datetime
except ImportError:
    raise unittest.SkipTest("Frappe bench required for API integration tests")

from hospital_operations.api import allocation
from hospital_operations.api import beds as beds_api
from hospital_operations.api import housekeeping as housekeeping_api
from hospital_operations.api import post_operative as post_operative_api
from hospital_operations.api import requests as requests_api
from hospital_operations.api import staffing as staffing_api
from hospital_operations.bed_allocation import housekeeping, operational
from hospital_operations.tests import factories


class TestOperationalAPI(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = factories.get_company()
        cls.unit_type = factories.make_service_unit_type()
        cls.ward = factories.make_ward(cls.company)
        cls.policy = factories.make_policy()

    def _patient_admission(self, inpatient=True):
        patient = factories.make_patient()
        record = None
        if inpatient:
            record = factories.make_inpatient_record(
                patient, self.unit_type, self.company
            )
        return patient, record

    def _request(self, care_level="ICU", source="Direct Admission", inpatient=True):
        patient, record = self._patient_admission(inpatient=inpatient)
        request = factories.make_request(
            patient,
            record,
            care_level=care_level,
            source=source,
            company=self.company,
        )
        return patient, record, request

    def test_beds_list_returns_operational_projection(self):
        bed = factories.make_bed(self.unit_type, "ICU", self.ward, self.company)

        result = beds_api.list(ward=self.ward.name)

        row = next(item for item in result["beds"] if item["name"] == bed.name)
        self.assertEqual(row["state"], "READY")
        self.assertEqual(row["care_level"], "ICU")
        self.assertTrue(row["allocatable"])
        self.assertFalse(row["occupied"])
        self.assertFalse(row["active_reservation"])
        self.assertGreaterEqual(result["total"], 1)

    def test_bed_availability_filters_ready_beds(self):
        ready = factories.make_bed(self.unit_type, "ICU", self.ward, self.company)
        blocked = factories.make_bed(self.unit_type, "ICU", self.ward, self.company)
        operational.block_bed(blocked.name, reason="API test block")

        result = beds_api.availability(ward=self.ward.name)

        names = {row["name"] for row in result["beds"]}
        self.assertIn(ready.name, names)
        self.assertNotIn(blocked.name, names)

    def test_bed_availability_for_request_reports_rejection_reasons(self):
        general = factories.make_bed(self.unit_type, "General", self.ward, self.company)
        _, _, request = self._request(care_level="ICU")

        result = beds_api.availability(request=request.name, ward=self.ward.name)

        row = next(item for item in result["beds"] if item["bed"] == general.name)
        self.assertFalse(row["eligible"])
        self.assertIn("CARE_LEVEL_MISMATCH", {reason["code"] for reason in row["reasons"]})

    def test_requests_create_is_idempotent_and_audited(self):
        patient, record = self._patient_admission()

        first = requests_api.create(
            patient=patient.name,
            inpatient_record=record.name,
            source="ED",
            priority="Urgent",
            care_level="ICU",
            idempotency_key="api-request-create-1",
            requirements=[
                {
                    "requirement_type": "VENTILATOR",
                    "operator": "=",
                    "required_value": "1",
                    "mandatory": 1,
                    "clinical_reason": "Respiratory support",
                }
            ],
        )
        second = requests_api.create(
            patient=patient.name,
            inpatient_record=record.name,
            source="ED",
            priority="Urgent",
            care_level="ICU",
            idempotency_key="api-request-create-1",
        )

        self.assertEqual(first["name"], second["name"])
        self.assertEqual(first["status"], "PENDING")
        self.assertEqual(first["care_level"], "ICU")
        self.assertTrue(
            frappe.db.exists(
                "Bed Allocation Event",
                {"event_type": "REQUEST_CREATED", "allocation_request": first["name"]},
            )
        )
        self.assertEqual(
            frappe.db.count(
                "Bed Clinical Requirement",
                {"parent": first["name"], "requirement_type": "VENTILATOR"},
            ),
            1,
        )

    def test_requests_create_rejects_reused_key_for_another_patient(self):
        patient, record = self._patient_admission()
        other_patient, _ = self._patient_admission(inpatient=False)
        requests_api.create(
            patient=patient.name,
            inpatient_record=record.name,
            idempotency_key="api-request-reused-key",
        )

        with self.assertRaises(frappe.ValidationError):
            requests_api.create(
                patient=other_patient.name,
                idempotency_key="api-request-reused-key",
            )

    def test_requests_list_paginates_and_filters(self):
        patient, record = self._patient_admission()
        for index in range(3):
            factories.make_request(
                patient, record, company=self.company, source="OPD"
            )

        result = requests_api.list(patient=patient.name, limit=2, start=0)

        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["data"]), 2)
        self.assertTrue(result["has_next"])
        self.assertTrue(all(row["patient"] == patient.name for row in result["data"]))

    def test_allocation_optimize_and_result(self):
        bed = factories.make_bed(self.unit_type, "ICU", self.ward, self.company)
        _, _, request = self._request()

        result = allocation.optimize(
            request_names=[request.name],
            policy=self.policy.name,
            bed_names=[bed.name],
        )

        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["assigned_count"], 1)
        self.assertEqual(result["unassigned_count"], 0)
        self.assertTrue(result["proposals"])

        detail = allocation.get_result(run=result["run"], limit=10)
        self.assertEqual(detail["run"]["name"], result["run"])
        self.assertEqual(len(detail["proposals"]), 1)
        self.assertEqual(detail["proposals"][0]["allocation_request"], request.name)

    def test_allocation_get_result_rejects_unknown_run(self):
        with self.assertRaises(frappe.ValidationError):
            allocation.get_result(run="RUN-DOES-NOT-EXIST")

    def test_housekeeping_api_lifecycle_releases_bed(self):
        bed = factories.make_bed(
            self.unit_type, "ICU", self.ward, self.company, state="VACATED"
        )
        task = housekeeping.ensure_task(bed.name)
        employee = factories.make_employee(self.company)

        started = housekeeping_api.start(
            task=task.name, assigned_employee=employee.name
        )
        self.assertEqual(started["status"], "IN_PROGRESS")
        self.assertEqual(operational.get_state_value(bed.name), "CLEANING_IN_PROGRESS")

        completed = housekeeping_api.complete(task=task.name)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(operational.get_state_value(bed.name), "CLEANING_IN_PROGRESS")

        verified = housekeeping_api.verify(task=task.name)
        self.assertEqual(verified["status"], "VERIFIED")
        self.assertEqual(operational.get_state_value(bed.name), "READY")

    def test_staffing_capacity_endpoint(self):
        policy = factories.make_policy(
            staffing={
                "ratio": {"ICU": 2},
                "competency": {"ICU": "ICU"},
                "minimum_nurses": {"ICU": 1},
            }
        )
        staffing_ward = factories.make_ward(self.company)
        factories.make_bed(self.unit_type, "ICU", staffing_ward, self.company)
        employee = factories.make_employee(self.company)
        shift = factories.make_shift_type("00:01:00", "23:58:00")
        factories.make_shift_assignment(employee, shift)
        factories.make_attendance(employee, "Present")
        factories.make_ward_nurse_assignment(employee, staffing_ward, "ICU")

        result = staffing_api.capacity(ward=staffing_ward.name, at=now_datetime())

        self.assertEqual(result["policy_version"], policy.version)
        ward = result["wards"][staffing_ward.name]
        self.assertTrue(ward["enabled"])
        self.assertEqual(ward["available_nurses"], 1)
        self.assertEqual(ward["qualified_nurses"], 1)
        self.assertEqual(ward["physical_beds"], 1)
        self.assertEqual(ward["staffed_bed_capacity"], 1)

    def test_post_operative_api_create_reschedule_and_cancel(self):
        bed = factories.make_bed(self.unit_type, "ICU", self.ward, self.company)
        _, _, request = self._request(source="OT")

        window_start = add_to_date(now_datetime(), hours=1)
        window_end = add_to_date(window_start, hours=4)
        created = post_operative_api.create(
            allocation_request=request.name,
            bed=bed.name,
            start_at=window_start,
            expires_at=window_end,
            idempotency_key="api-postop-create-1",
        )

        self.assertEqual(created["reservation_type"], "POST_OPERATIVE")
        self.assertEqual(created["status"], "ACTIVE")
        self.assertEqual(operational.get_state_value(bed.name), "RESERVED")

        new_start = add_to_date(window_start, hours=1)
        new_end = add_to_date(new_start, hours=4)
        rescheduled = post_operative_api.reschedule(
            reservation=created["reservation"],
            start_at=new_start,
            expires_at=new_end,
            reason="Surgery delayed",
        )
        self.assertEqual(rescheduled["start_at"], str(new_start))

        cancelled = post_operative_api.cancel(
            reservation=created["reservation"], reason="Surgery cancelled"
        )
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(operational.get_state_value(bed.name), "READY")
