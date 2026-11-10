import frappe
from frappe import _
from frappe.model.document import Document


class BedAllocationEvent(Document):
    """Append-only audit record.

    Creation is allowed (typically through the audit helper); modification and
    deletion are rejected so the operational history cannot be rewritten.
    """

    def on_update(self):
        if self.flags.in_insert:
            return
        frappe.throw(_("Bed Allocation Event records are append-only."))

    def on_trash(self):
        frappe.throw(_("Bed Allocation Event records cannot be deleted."))
