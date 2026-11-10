import frappe
from frappe.model.document import Document
from frappe.utils import now

from hospital_operations.bed_allocation.state import (
    InvalidBedTransition,
    assert_transition,
)


class BedOperationalState(Document):
    def before_insert(self):
        self.version = 1
        self.last_changed_by = frappe.session.user
        self.last_changed_at = now()
        if self.state == "READY" and not self.ready_at:
            self.ready_at = now()

    def before_save(self):
        if self.is_new():
            return
        previous = self.get_doc_before_save()
        if not previous or previous.state == self.state:
            return
        try:
            assert_transition(previous.state, self.state)
        except InvalidBedTransition as exc:
            frappe.throw(str(exc))
        self.version = int(previous.version or 0) + 1
        self.last_changed_by = frappe.session.user
        self.last_changed_at = now()
        if self.state == "READY":
            self.ready_at = now()
