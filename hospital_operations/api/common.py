"""Shared validation, pagination and role helpers for backend APIs."""

import json

import frappe
from frappe import _
from frappe.utils import cint

from hospital_operations.utils.roles import require_any_role

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def require_api_role(*roles):
    require_any_role(*roles)


def pagination(limit=None, start=None):
    """Return a validated ``(limit, start)`` pair."""

    if limit in (None, ""):
        limit = DEFAULT_LIMIT
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        frappe.throw(_("limit must be an integer."), frappe.ValidationError)
    if limit <= 0:
        frappe.throw(_("limit must be greater than zero."), frappe.ValidationError)
    limit = min(limit, MAX_LIMIT)

    try:
        start = int(start or 0)
    except (TypeError, ValueError):
        frappe.throw(_("start must be an integer."), frappe.ValidationError)
    if start < 0:
        frappe.throw(_("start cannot be negative."), frappe.ValidationError)
    return limit, start


def has_next(start, count, total):
    return (start + count) < total


def as_bool(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    frappe.throw(_("Expected a boolean value."), frappe.ValidationError)


def split_names(value):
    """Accept a comma-separated string or a list of names, preserving order."""

    if value in (None, ""):
        return []
    if isinstance(value, str):
        values = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple)):
        values = [str(item).strip() for item in value]
    else:
        frappe.throw(_("Expected a name or list of names."), frappe.ValidationError)
    return list(dict.fromkeys(item for item in values if item))


def parse_json(value, fieldname):
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            frappe.throw(
                _("{0} must be valid JSON.").format(fieldname),
                frappe.ValidationError,
            )
    frappe.throw(_("{0} must be valid JSON.").format(fieldname), frappe.ValidationError)


def optional_int(value, fieldname):
    if value in (None, ""):
        return None
    try:
        return cint(value)
    except (TypeError, ValueError):
        frappe.throw(
            _("{0} must be an integer.").format(fieldname), frappe.ValidationError
        )


def require_value(value, fieldname):
    if value in (None, ""):
        frappe.throw(_("{0} is required.").format(fieldname), frappe.ValidationError)
    return value
