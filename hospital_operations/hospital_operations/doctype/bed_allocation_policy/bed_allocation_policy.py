import json

import frappe
from frappe import _
from frappe.model.document import Document

JSON_FIELDS = (
    "clinical_priority_rules",
    "eligibility_rules",
    "cost_weights",
    "waiting_time_rules",
    "manual_override_permissions",
)


class BedAllocationPolicy(Document):
    def validate(self):
        self._validate_unique_version()
        self._validate_json_fields()

    def _validate_unique_version(self):
        duplicate = frappe.db.exists(
            "Bed Allocation Policy",
            {
                "policy_name": self.policy_name,
                "version": self.version,
                "name": ("!=", self.name),
            },
        )
        if duplicate:
            frappe.throw(
                _("Policy {0} version {1} already exists ({2}).").format(
                    self.policy_name, self.version, duplicate
                ),
                frappe.DuplicateEntryError,
            )

    def _validate_json_fields(self):
        for fieldname in JSON_FIELDS:
            value = self.get(fieldname)
            if not value:
                continue
            try:
                json.loads(value)
            except (TypeError, ValueError):
                frappe.throw(
                    _("{0} must contain valid JSON.").format(
                        self.meta.get_label(fieldname)
                    )
                )
