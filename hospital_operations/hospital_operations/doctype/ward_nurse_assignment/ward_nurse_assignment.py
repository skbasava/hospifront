import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class WardNurseAssignment(Document):
    def validate(self):
        self._validate_dates()
        self._validate_unique_assignment()

    def _validate_dates(self):
        if self.valid_from and self.valid_to:
            if getdate(self.valid_to) < getdate(self.valid_from):
                frappe.throw(_("Valid To cannot be before Valid From."))

    def _validate_unique_assignment(self):
        if not self.valid_from:
            return
        duplicate = frappe.db.exists(
            "Ward Nurse Assignment",
            {
                "employee": self.employee,
                "ward": self.ward,
                "valid_from": self.valid_from,
                "name": ("!=", self.name),
            },
        )
        if duplicate:
            frappe.throw(
                _("This employee/ward/start-date assignment already exists ({0}).").format(
                    duplicate
                ),
                frappe.DuplicateEntryError,
            )
