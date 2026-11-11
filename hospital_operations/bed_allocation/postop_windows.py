"""Pure time-window helpers for post-operative bed holds."""

from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_HOLD_HOURS = 4


class WindowError(ValueError):
    """A reservation window is invalid."""


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise WindowError(f"Invalid datetime: {value}") from exc
    raise WindowError("A datetime value is required.")


def windows_overlap(a_start, a_end, b_start, b_end):
    """Return True when two half-open windows intersect.

    Windows are half-open so a handover may start exactly when the previous
    hold ends. Adjacent windows are therefore not a conflict.
    """

    a_start = _coerce_datetime(a_start)
    a_end = _coerce_datetime(a_end)
    b_start = _coerce_datetime(b_start)
    b_end = _coerce_datetime(b_end)
    if a_end <= a_start or b_end <= b_start:
        raise WindowError("A reservation window must end after it starts.")
    return a_start < b_end and b_start < a_end


@dataclass(frozen=True)
class ReservationWindow:
    start_at: datetime
    end_at: datetime

    def __post_init__(self):
        start_at = _coerce_datetime(self.start_at)
        end_at = _coerce_datetime(self.end_at)
        if end_at <= start_at:
            raise WindowError("A reservation window must end after it starts.")
        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "end_at", end_at)

    def overlaps(self, other):
        return windows_overlap(
            self.start_at, self.end_at, other.start_at, other.end_at
        )

    def as_dict(self):
        return {
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
        }


def default_window(start_at, hold_hours=DEFAULT_HOLD_HOURS):
    start_at = _coerce_datetime(start_at)
    return ReservationWindow(start_at, start_at + timedelta(hours=hold_hours))
