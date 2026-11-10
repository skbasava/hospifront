import frappe

DEFAULT_POLICY_CACHE_KEY = "hospital_operations:active_policy"


def get_active_policy():
    """Return the newest enabled Bed Allocation Policy, or None.

    Clinical values live in the policy document, never in Python defaults.
    """
    policies = frappe.get_all(
        "Bed Allocation Policy",
        filters={"enabled": 1},
        fields=["name", "policy_name", "version"],
        order_by="modified desc",
        limit_page_length=1,
    )
    if not policies:
        return None
    return frappe.get_doc("Bed Allocation Policy", policies[0].name)
