import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now


class BedReservation(Document):
    def validate(self):
        self._validate_window()
        self._validate_idempotency()
        self._validate_active_immediate_claim()

    def _validate_window(self):
        if get_datetime(self.expires_at) <= get_datetime(self.start_at):
            frappe.throw(_("Expires At must be after Start At."))

    def _validate_idempotency(self):
        duplicate = frappe.db.exists(
            "Bed Reservation",
            {"idempotency_key": self.idempotency_key, "name": ("!=", self.name)},
        )
        if duplicate:
            frappe.throw(
                _("Idempotency key {0} is already used by {1}.").format(
                    self.idempotency_key, duplicate
                ),
                frappe.DuplicateEntryError,
            )

    def _validate_active_immediate_claim(self):
        if self.status != "ACTIVE" or self.reservation_type != "IMMEDIATE":
            return
        conflict = frappe.db.exists(
            "Bed Reservation",
            {
                "healthcare_service_unit": self.healthcare_service_unit,
                "status": "ACTIVE",
                "reservation_type": "IMMEDIATE",
                "name": ("!=", self.name),
            },
        )
        if conflict:
            frappe.throw(
                _("Bed {0} already has an active immediate claim ({1}).").format(
                    self.healthcare_service_unit, conflict
                ),
                frappe.ValidationError,
            )

    def on_update(self):
        if self.status == "CONFIRMED" and not self.confirmed_at:
            self.db_set("confirmed_at", now(), update_modified=False)
