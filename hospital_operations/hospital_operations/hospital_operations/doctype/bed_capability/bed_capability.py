import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now


class BedCapability(Document):
    def validate(self):
        if self.valid_from and self.valid_until:
            if get_datetime(self.valid_until) <= get_datetime(self.valid_from):
                frappe.throw(_("Valid Until must be after Valid From."))

    def is_current(self):
        current = get_datetime(now())
        if self.valid_from and get_datetime(self.valid_from) > current:
            return False
        if self.valid_until and get_datetime(self.valid_until) <= current:
            return False
        return bool(self.enabled)
