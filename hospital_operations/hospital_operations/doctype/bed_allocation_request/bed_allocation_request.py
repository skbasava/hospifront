import frappe
from frappe import _
from frappe.model.document import Document


class BedAllocationRequest(Document):
    def validate(self):
        self._validate_idempotent_source()
        self._validate_clinical_requirements()

    def _validate_idempotent_source(self):
        if not self.idempotency_key:
            return
        duplicate = frappe.db.exists(
            "Bed Allocation Request",
            {"idempotency_key": self.idempotency_key, "name": ("!=", self.name)},
        )
        if duplicate:
            frappe.throw(
                _("Idempotency key {0} is already used by {1}.").format(
                    self.idempotency_key, duplicate
                ),
                frappe.DuplicateEntryError,
            )

    def _validate_clinical_requirements(self):
        seen = set()
        for row in self.clinical_requirements or []:
            key = (row.requirement_type, row.operator, row.required_value or "")
            if key in seen:
                frappe.throw(
                    _("Duplicate clinical requirement: {0}").format(row.requirement_type)
                )
            seen.add(key)
