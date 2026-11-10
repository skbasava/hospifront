"""Read-only Frappe REST client for schema discovery and integration checks.

Target selection:
    FRAPPE_TARGET=local|remote (default: remote)

Local target:
    FRAPPE_LOCAL_BASE_URL (default: http://127.0.0.1:8000)
    FRAPPE_LOCAL_API_KEY
    FRAPPE_LOCAL_API_SECRET
    FRAPPE_LOCAL_SITE (optional label)
    FRAPPE_LOCAL_TIMEOUT (optional, default 15)

Remote target:
    FRAPPE_REMOTE_BASE_URL or legacy FRAPPE_BASE_URL
    FRAPPE_REMOTE_API_KEY or legacy FRAPPE_API_KEY
    FRAPPE_REMOTE_API_SECRET or legacy FRAPPE_API_SECRET
    FRAPPE_REMOTE_SITE (optional label)
    FRAPPE_REMOTE_TIMEOUT or legacy FRAPPE_TIMEOUT (optional, default 15)

The client never prints or logs credential values, and TLS verification is
always enabled. Only GET requests are permitted in discovery mode.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests

TARGETS = ("local", "remote")
DEFAULT_TARGET = "remote"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8000"


class FrappeClientError(Exception):
    """Structured error raised for Frappe/HTTP failures."""

    def __init__(self, status: int | None, message: str, detail: Any = None):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.detail = detail


@dataclass
class ClientConfig:
    base_url: str
    api_key: str
    api_secret: str
    timeout: float = 15.0
    target: str = DEFAULT_TARGET
    site: str | None = None

    @classmethod
    def from_env(cls, target: str | None = None) -> ClientConfig:
        target = _normalise_target(target or os.environ.get("FRAPPE_TARGET", DEFAULT_TARGET))

        if target == "local":
            base_url = (
                os.environ.get("FRAPPE_LOCAL_BASE_URL") or DEFAULT_LOCAL_BASE_URL
            ).strip().rstrip("/")
            api_key_name = "FRAPPE_LOCAL_API_KEY"
            api_secret_name = "FRAPPE_LOCAL_API_SECRET"
            missing = [
                name for name in (api_key_name, api_secret_name) if not os.environ.get(name)
            ]
            if missing:
                raise FrappeClientError(
                    None,
                    f"Missing environment variables for local target: {', '.join(missing)}",
                )
            api_key = os.environ[api_key_name]
            api_secret = os.environ[api_secret_name]
            timeout = _parse_timeout(
                os.environ.get("FRAPPE_LOCAL_TIMEOUT") or os.environ.get("FRAPPE_TIMEOUT")
            )
            site = os.environ.get("FRAPPE_LOCAL_SITE") or None
        else:
            base_url = (
                os.environ.get("FRAPPE_REMOTE_BASE_URL")
                or os.environ.get("FRAPPE_BASE_URL")
                or ""
            ).strip().rstrip("/")
            api_key = (
                os.environ.get("FRAPPE_REMOTE_API_KEY") or os.environ.get("FRAPPE_API_KEY") or ""
            )
            api_secret = (
                os.environ.get("FRAPPE_REMOTE_API_SECRET")
                or os.environ.get("FRAPPE_API_SECRET")
                or ""
            )
            missing = [
                name
                for name, value in (
                    ("FRAPPE_REMOTE_BASE_URL or FRAPPE_BASE_URL", base_url),
                    ("FRAPPE_REMOTE_API_KEY or FRAPPE_API_KEY", api_key),
                    ("FRAPPE_REMOTE_API_SECRET or FRAPPE_API_SECRET", api_secret),
                )
                if not value
            ]
            if missing:
                raise FrappeClientError(
                    None,
                    f"Missing environment variables for remote target: {', '.join(missing)}",
                )
            timeout = _parse_timeout(
                os.environ.get("FRAPPE_REMOTE_TIMEOUT") or os.environ.get("FRAPPE_TIMEOUT")
            )
            site = os.environ.get("FRAPPE_REMOTE_SITE") or None

        return cls(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            timeout=timeout,
            target=target,
            site=site,
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "site": self.site,
            "base_url": self.base_url,
            "api_key": "***redacted***",
            "api_secret": "***redacted***",
            "timeout": self.timeout,
        }


def _normalise_target(value: str | None) -> str:
    target = str(value or "").strip().lower()
    if target not in TARGETS:
        raise FrappeClientError(
            None,
            f"Invalid FRAPPE_TARGET {value!r}; expected one of: {', '.join(TARGETS)}",
        )
    return target


def _parse_timeout(value: str | None) -> float:
    try:
        timeout = float(value or "15")
    except ValueError as exc:
        raise FrappeClientError(None, "Invalid FRAPPE timeout", value) from exc
    if timeout <= 0:
        raise FrappeClientError(None, "FRAPPE timeout must be positive", value)
    return timeout


class FrappeClient:
    """Read-only client. Mutating verbs are intentionally not implemented."""

    def __init__(self, config: ClientConfig | None = None, target: str | None = None):
        self.config = config or ClientConfig.from_env(target)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"token {self.config.api_key}:{self.config.api_secret}",
                "Accept": "application/json",
                "User-Agent": "hospital-operations-discovery/0.1",
            }
        )

    # -- core ---------------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None, max_retries: int = 2) -> Any:
        url = f"{self.config.base_url}{path}"
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = self.session.get(
                    url, params=params, timeout=self.config.timeout, verify=True
                )
            except requests.exceptions.SSLError as exc:
                raise FrappeClientError(None, "TLS verification failed", str(exc)) from exc
            except requests.exceptions.Timeout as exc:
                last_err = exc
                if attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise FrappeClientError(None, "Request timed out", str(exc)) from exc
            except requests.exceptions.ConnectionError as exc:
                raise FrappeClientError(None, "Connection error", str(exc)) from exc

            if resp.status_code == 429 and attempt < max_retries:
                time.sleep(float(resp.headers.get("Retry-After", 2)))
                continue
            if resp.status_code >= 400:
                detail: Any
                try:
                    detail = resp.json()
                except ValueError:
                    detail = resp.text[:500]
                raise FrappeClientError(resp.status_code, _describe(resp.status_code), detail)
            try:
                return resp.json()
            except ValueError as exc:
                raise FrappeClientError(resp.status_code, "Non-JSON response", resp.text[:500]) from exc
        raise FrappeClientError(None, "Request failed", str(last_err))

    # -- public API ---------------------------------------------------------
    def authenticate(self) -> str:
        data = self.call_method("frappe.auth.get_logged_user")
        return data.get("message") if isinstance(data, dict) else str(data)

    def get_versions(self) -> dict[str, Any]:
        data = self.call_method("frappe.utils.change_log.get_versions")
        return data.get("message", data) if isinstance(data, dict) else {}

    def call_method(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        # Discovery is GET-only; payload converted to query string.
        return self._get(f"/api/method/{method}", params=payload)

    def get_list(
        self,
        doctype: str,
        fields: list[str] | None = None,
        filters: Any = None,
        limit: int = 0,
        order_by: str | None = None,
        start: int = 0,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "doctype": doctype,
            "limit_page_length": limit,
            "limit_start": start,
        }
        if fields:
            params["fields"] = _json(fields)
        if filters:
            params["filters"] = _json(filters)
        if order_by:
            params["order_by"] = order_by
        data = self.call_method("frappe.client.get_list", params)
        return data.get("message", []) if isinstance(data, dict) else []

    def get_doctype_metadata(self, doctype: str) -> dict[str, Any]:
        data = self._get(f"/api/resource/DocType/{doctype}")
        return data.get("data", data) if isinstance(data, dict) else data

    def get_resource(self, doctype: str, name: str) -> dict[str, Any]:
        data = self._get(f"/api/resource/{doctype}/{name}")
        return data.get("data", data) if isinstance(data, dict) else data

    def check_health(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for method in ("frappe.ping", "frappe.auth.get_logged_user"):
            try:
                out[method] = self.call_method(method)
            except FrappeClientError as exc:
                out[method] = {"error": exc.message, "status": exc.status}
        return out


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


def _describe(status: int) -> str:
    return {
        401: "Unauthorized (invalid or missing token)",
        403: "Forbidden (insufficient permissions)",
        404: "Not found",
        417: "Expectation failed (Frappe validation)",
        429: "Rate limited",
        500: "Internal server error",
    }.get(status, f"HTTP {status}")
