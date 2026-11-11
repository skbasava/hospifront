"""Pure staffing-capacity rules for operational bed allocation.

The module deliberately has no Frappe imports: hospital-specific ratios,
competencies and presence policy come from the versioned ``Bed Allocation
Policy`` document and are evaluated here so the rules can be unit-tested and
reused by background jobs.

A nurse contributes to capacity only when they are active, assigned to the
ward, on shift, present according to the configured source, and (for
``qualified_nurses``) hold the ward's required competency. Scheduling alone is
never treated as presence unless the policy explicitly says so.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta

PRESENCE_SOURCES = frozenset({"attendance", "checkin", "scheduled"})
DEFAULT_PRESENCE_SOURCE = "attendance"


class StaffingRulesError(ValueError):
    """The staffing policy is malformed or unsafe to evaluate."""


@dataclass(frozen=True)
class StaffingRules:
    ratio: dict
    competency: dict
    minimum_nurses: dict
    presence_source: str = DEFAULT_PRESENCE_SOURCE
    count_half_day: bool = False
    count_work_from_home: bool = True

    def patients_per_nurse(self, care_level):
        return self.ratio.get(care_level) or self.ratio.get("Other") or 1

    def required_competency(self, care_level):
        return self.competency.get(care_level) or self.competency.get("Other")

    def minimum_for(self, care_level):
        return int(
            self.minimum_nurses.get(care_level)
            or self.minimum_nurses.get("Other")
            or 0
        )


@dataclass(frozen=True)
class NurseSignal:
    """One nurse's state at the evaluation instant.

    The integration layer resolves shift windows, attendance, leave and check-in
    records; this object keeps the pure computation deterministic.
    """

    employee: str
    ward: str
    competencies: frozenset = frozenset()
    active: bool = True
    on_leave: bool = False
    on_shift: bool = True
    present: bool = True
    attendance_status: str | None = None
    shift_type: str | None = None


@dataclass(frozen=True)
class StaffingResult:
    ward: str
    care_level: str
    enabled: bool
    available_nurses: int = 0
    qualified_nurses: int = 0
    current_patients: int = 0
    physical_beds: int = 0
    staffed_bed_capacity: int = 0
    available_capacity: int = 0
    shift: str | None = None
    policy_version: str | None = None
    exclusions: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


def parse_rules(raw):
    """Parse the ``staffing`` object from a policy's ``eligibility_rules``.

    Returns ``None`` when the policy does not configure staffing. In that case
    callers must not invent a clinical ratio or block allocation.
    """

    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise StaffingRulesError("eligibility_rules is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise StaffingRulesError("eligibility_rules must be a JSON object.")

    staffing = raw.get("staffing")
    if not staffing:
        return None
    if not isinstance(staffing, dict):
        raise StaffingRulesError("staffing rules must be a JSON object.")

    presence_source = staffing.get("presence_source") or DEFAULT_PRESENCE_SOURCE
    if presence_source not in PRESENCE_SOURCES:
        raise StaffingRulesError(
            f"Unknown staffing presence_source: {presence_source}."
        )

    return StaffingRules(
        ratio=_positive_int_map(staffing.get("ratio"), "ratio"),
        competency=_string_map(staffing.get("competency"), "competency"),
        minimum_nurses=_non_negative_int_map(
            staffing.get("minimum_nurses"), "minimum_nurses"
        ),
        presence_source=presence_source,
        count_half_day=bool(staffing.get("count_half_day", False)),
        count_work_from_home=bool(staffing.get("count_work_from_home", True)),
    )


def resolve_presence(rules, attendance_status):
    """Return whether an attendance status counts as present.

    Check-in presence is resolved by the HRMS adapter; this helper is used for
    the ``attendance`` and ``scheduled`` sources.
    """

    if rules.presence_source == "scheduled":
        return True

    status = (attendance_status or "").strip()
    if status == "Present":
        return True
    if status == "Work From Home":
        return rules.count_work_from_home
    if status == "Half Day":
        return rules.count_half_day
    return False


def coerce_time(value):
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return time(hours % 24, minutes, seconds)
    if isinstance(value, str):
        parts = value.strip().split(":")
        try:
            numbers = [int(part) for part in parts]
        except (TypeError, ValueError) as exc:
            raise StaffingRulesError(f"Invalid shift time: {value!r}.") from exc
        while len(numbers) < 3:
            numbers.append(0)
        try:
            return time(numbers[0], numbers[1], numbers[2])
        except ValueError as exc:
            raise StaffingRulesError(f"Invalid shift time: {value!r}.") from exc
    raise StaffingRulesError(f"Invalid shift time: {value!r}.")


def shift_window(start_time, end_time, on_date):
    """Return the absolute start/end datetimes for a shift on ``on_date``.

    Overnight shifts (end <= start) end on the following calendar day.
    """

    if isinstance(on_date, datetime):
        on_date = on_date.date()
    if not isinstance(on_date, date):
        raise StaffingRulesError("A date is required to resolve a shift window.")
    start = datetime.combine(on_date, coerce_time(start_time))
    end = datetime.combine(on_date, coerce_time(end_time))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def is_within_shift(start_time, end_time, at):
    """Return whether ``at`` falls inside today's or yesterday's shift window."""

    if not isinstance(at, datetime):
        raise StaffingRulesError("A datetime is required to check a shift.")
    for offset in (0, -1):
        on_date = at.date() + timedelta(days=offset)
        start, end = shift_window(start_time, end_time, on_date)
        if start <= at < end:
            return True
    return False


def compute(
    rules,
    *,
    ward,
    care_level,
    nurses,
    current_patients=0,
    physical_beds=0,
    shift=None,
    policy_version=None,
):
    """Compute staffed bed capacity from already-resolved nurse signals."""

    exclusions = {}
    available = 0
    qualified = 0
    required = rules.required_competency(care_level)

    for signal in sorted(nurses, key=lambda row: row.employee):
        code = _exclusion_code(signal, ward)
        if code:
            exclusions[code] = exclusions.get(code, 0) + 1
            continue
        available += 1
        if required and required not in signal.competencies:
            exclusions["WRONG_COMPETENCY"] = (
                exclusions.get("WRONG_COMPETENCY", 0) + 1
            )
            continue
        qualified += 1

    minimum = rules.minimum_for(care_level)
    notes = {"required_competency": required}
    if qualified < minimum:
        capacity = 0
        notes["below_minimum_nurses"] = {
            "minimum": minimum,
            "qualified": qualified,
        }
    else:
        ratio = rules.patients_per_nurse(care_level)
        capacity = min(int(physical_beds), int(qualified * ratio))
        notes["patients_per_nurse"] = ratio

    current_patients = max(int(current_patients or 0), 0)
    return StaffingResult(
        ward=ward,
        care_level=care_level,
        enabled=True,
        available_nurses=available,
        qualified_nurses=qualified,
        current_patients=current_patients,
        physical_beds=int(physical_beds or 0),
        staffed_bed_capacity=capacity,
        available_capacity=max(capacity - current_patients, 0),
        shift=shift,
        policy_version=str(policy_version) if policy_version else None,
        exclusions={key: exclusions[key] for key in sorted(exclusions)},
        notes=notes,
    )


def _exclusion_code(signal, ward):
    if not signal.active:
        return "INACTIVE_EMPLOYEE"
    if signal.on_leave:
        return "ON_LEAVE"
    if signal.ward != ward:
        return "WRONG_WARD"
    if not signal.on_shift:
        return "OFF_SHIFT"
    if not signal.present:
        status = (signal.attendance_status or "").strip().upper()
        if status == "ABSENT":
            return "ABSENT"
        if status == "ON LEAVE":
            return "ON_LEAVE"
        if status == "HALF DAY":
            return "HALF_DAY_NOT_COUNTED"
        if status == "WORK FROM HOME":
            return "WORK_FROM_HOME_NOT_COUNTED"
        return "NOT_PRESENT"
    return None


def _positive_int_map(value, fieldname):
    result = _non_negative_int_map(value, fieldname)
    for key, number in result.items():
        if number <= 0:
            raise StaffingRulesError(f"{fieldname}.{key} must be greater than zero.")
    return result


def _non_negative_int_map(value, fieldname):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StaffingRulesError(f"{fieldname} must be a JSON object.")
    result = {}
    for key, number in value.items():
        try:
            parsed = int(number)
        except (TypeError, ValueError) as exc:
            raise StaffingRulesError(
                f"{fieldname}.{key} must be an integer."
            ) from exc
        if parsed < 0:
            raise StaffingRulesError(f"{fieldname}.{key} cannot be negative.")
        result[str(key)] = parsed
    return result


def _string_map(value, fieldname):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StaffingRulesError(f"{fieldname} must be a JSON object.")
    result = {}
    for key, text in value.items():
        if text is None or not str(text).strip():
            raise StaffingRulesError(f"{fieldname}.{key} must be a non-empty string.")
        result[str(key)] = str(text).strip()
    return result
