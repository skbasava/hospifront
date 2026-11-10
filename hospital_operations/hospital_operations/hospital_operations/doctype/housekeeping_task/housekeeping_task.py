import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now

ALLOWED_TRANSITIONS = {
    "PENDING": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
    "COMPLETED": {"VERIFIED", "CANCELLED"},
    "VERIFIED": set(),
    "CANCELLED": set(),
}


class HousekeepingTask(Document):
    def before_insert(self):
        if self.status == "IN_PROGRESS" and not self.started_at:
            self.started_at = now()

    def validate(self):
        self._validate_status_transition()
        if self.status in ("COMPLETED", "VERIFIED") and not self.completed_at:
            self.completed_at = now()
        if self.status == "VERIFIED":
            if not self.verified_by:
                self.verified_by = frappe.session.user
            if not self.verified_at:
                self.verified_at = now()

    def _validate_status_transition(self):
        previous = self.get_doc_before_save()
        if not previous or previous.status == self.status:
            return
        if self.status not in ALLOWED_TRANSITIONS.get(previous.status, set()):
            frappe.throw(
                _("Invalid housekeeping status transition: {0} -> {1}").format(
                    previous.status, self.status
                )
            )
