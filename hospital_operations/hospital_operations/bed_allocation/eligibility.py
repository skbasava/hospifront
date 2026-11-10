"""Stage 1 — mandatory clinical eligibility.

Pure Python: no Frappe imports, so every rule can be tested without a site.
A patient-bed pair is eligible only when all mandatory constraints pass. Cost
penalties are never allowed to override a rejection produced here.
"""

from dataclasses import dataclass, field
from typing import Mapping

TRUTHY_VALUES = frozenset(
    {"1", "true", "yes", "y", "on", "available", "present", "supported"}
)
FALSY_VALUES = frozenset(
    {"", "0", "false", "no", "n", "off", "unavailable", "absent", "none"}
)

CARE_LEVELS = ("ICU", "General", "Special", "Other")

CARE_LEVEL_COMPATIBILITY = {
    "ICU": frozenset({"ICU"}),
    "General": frozenset({"General"}),
    "Special": frozenset({"Special"}),
    "Other": frozenset({"ICU", "General", "Special", "Other"}),
}

READY_STATE = "READY"
STATE_REASON_CODES = {
    "MAINTENANCE": ("BED_MAINTENANCE", "Bed is under maintenance"),
    "BLOCKED": ("BED_BLOCKED", "Bed is blocked"),
    "VACATED": ("BED_NEEDS_CLEANING", "Bed has been vacated and requires cleaning"),
    "CLEANING_REQUIRED": ("BED_NEEDS_CLEANING", "Bed requires cleaning"),
    "CLEANING_IN_PROGRESS": ("BED_CLEANING", "Bed cleaning is in progress"),
    "ISOLATION_CLEANING": ("BED_ISOLATION_CLEANING", "Bed isolation cleaning is in progress"),
    "OCCUPIED": ("BED_OCCUPIED", "Bed is occupied"),
    "RESERVED": ("BED_RESERVED", "Bed is reserved"),
}


@dataclass(frozen=True)
class ClinicalRequirement:
    requirement_type: str
    required_value: str = ""
    operator: str = "="
    mandatory: bool = True
    clinical_reason: str = ""


@dataclass(frozen=True)
class RequestContext:
    name: str
    care_level: str
    priority: str = "Routine"
    medical_department: str | None = None
    requirements: tuple[ClinicalRequirement, ...] = ()
    requested_at: str | None = None
    source: str = "Direct Admission"


@dataclass(frozen=True)
class BedContext:
    name: str
    care_level: str
    state: str
    allocatable: bool = True
    occupied: bool = False
    active_reservation: bool = False
    capabilities: Mapping[str, frozenset[str]] = field(default_factory=dict)
    specialties: frozenset[str] = frozenset()
    ward: str | None = None
    room_sharing_ok: bool = True
    staffed_capacity_available: bool | None = None
    state_version: int | None = None


@dataclass(frozen=True)
class Rejection:
    code: str
    message: str


@dataclass
class EligibilityResult:
    request: str
    bed: str
    eligible: bool
    reasons: list[Rejection] = field(default_factory=list)
    matched_requirements: list[str] = field(default_factory=list)
    optional_gaps: list[Rejection] = field(default_factory=list)

    def as_dict(self):
        return {
            "request": self.request,
            "bed": self.bed,
            "eligible": self.eligible,
            "reasons": [
                {"code": reason.code, "message": reason.message}
                for reason in self.reasons
            ],
            "matched_requirements": list(self.matched_requirements),
            "optional_gaps": [
                {"code": gap.code, "message": gap.message}
                for gap in self.optional_gaps
            ],
        }


def _normalise(value):
    return str(value).strip().casefold()


def _capability_values(bed, capability_type):
    wanted = _normalise(capability_type)
    for key, values in bed.capabilities.items():
        if _normalise(key) == wanted:
            return frozenset(_normalise(value) for value in values)
    return frozenset()


def _is_truthy(values):
    return any(value not in FALSY_VALUES for value in values)


def _numeric_values(values):
    numbers = []
    for value in values:
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            continue
    return numbers


def _satisfies(requirement, values):
    operator = (requirement.operator or "=").strip().upper()
    required = _normalise(requirement.required_value)

    if operator == "=":
        if required in TRUTHY_VALUES:
            return _is_truthy(values)
        return required in values

    if operator == "!=":
        if required in TRUTHY_VALUES:
            return not _is_truthy(values)
        return required not in values

    if operator == "IN":
        wanted = {
            _normalise(value)
            for value in str(requirement.required_value).split(",")
            if _normalise(value)
        }
        return bool(wanted.intersection(values))

    if operator == "NOT IN":
        wanted = {
            _normalise(value)
            for value in str(requirement.required_value).split(",")
            if _normalise(value)
        }
        return not bool(wanted.intersection(values))

    if operator in (">=", "<="):
        try:
            target = float(requirement.required_value)
        except (TypeError, ValueError):
            return False
        numbers = _numeric_values(values)
        if operator == ">=":
            return any(number >= target for number in numbers)
        return any(number <= target for number in numbers)

    return False


def care_level_compatible(request_level, bed_level, compatibility=None):
    """Check care-level compatibility.

    ``compatibility`` is expected to come from Bed Allocation Policy in later
    phases. The built-in map is a conservative default, not a hospital policy.
    """
    mapping = compatibility or CARE_LEVEL_COMPATIBILITY
    allowed = mapping.get(request_level)
    if allowed is None:
        return False
    if isinstance(allowed, str):
        allowed = frozenset({allowed})
    return bed_level in allowed


def _specialty_match(request, bed):
    if not request.medical_department or not bed.specialties:
        return True
    requested = _normalise(request.medical_department)
    return requested in {_normalise(value) for value in bed.specialties}


def _requirement_values(requirement, bed):
    capability_type = requirement.requirement_type
    if capability_type == "ICU_LEVEL":
        return frozenset({_normalise(bed.care_level)}).union(
            _capability_values(bed, capability_type)
        )
    if capability_type == "SPECIALTY":
        if bed.specialties:
            return frozenset(_normalise(value) for value in bed.specialties)
        return _capability_values(bed, capability_type)
    return _capability_values(bed, capability_type)


def evaluate(
    request: RequestContext,
    bed: BedContext,
    care_level_compatibility=None,
) -> EligibilityResult:
    reasons = []
    matched = []
    optional_gaps = []

    if not bed.allocatable:
        reasons.append(
            Rejection(
                "BED_NOT_ALLOCATABLE",
                "Bed is not marked as allocatable by hospital operations",
            )
        )

    if bed.state != READY_STATE:
        code, message = STATE_REASON_CODES.get(
            bed.state,
            ("BED_STATE_NOT_ALLOCATABLE", f"Bed state {bed.state} is not allocatable"),
        )
        reasons.append(Rejection(code, message))

    if bed.occupied:
        reasons.append(Rejection("BED_OCCUPIED", "Bed is currently occupied"))

    if bed.active_reservation:
        reasons.append(
            Rejection("BED_ACTIVE_RESERVATION", "Bed has an active reservation")
        )

    if bed.staffed_capacity_available is False:
        reasons.append(
            Rejection(
                "STAFFED_CAPACITY_EXHAUSTED",
                "Ward has no staffed bed capacity available",
            )
        )

    if not bed.room_sharing_ok:
        reasons.append(
            Rejection("ROOM_SHARING_CONFLICT", "Bed sharing constraints are not met")
        )

    if not care_level_compatible(
        request.care_level, bed.care_level, care_level_compatibility
    ):
        reasons.append(
            Rejection(
                "CARE_LEVEL_MISMATCH",
                f"Bed care level {bed.care_level} is not compatible with requested "
                f"{request.care_level}",
            )
        )

    if not _specialty_match(request, bed):
        reasons.append(
            Rejection(
                "SPECIALTY_MISMATCH",
                f"Bed specialty does not cover {request.medical_department}",
            )
        )

    for requirement in request.requirements:
        values = _requirement_values(requirement, bed)
        if _satisfies(requirement, values):
            matched.append(requirement.requirement_type)
            continue
        rejection = Rejection(
            "REQUIREMENT_NOT_MET"
            if requirement.mandatory
            else "OPTIONAL_REQUIREMENT_NOT_MET",
            f"{requirement.requirement_type} requirement not met "
            f"(needed {requirement.operator} {requirement.required_value})",
        )
        if requirement.mandatory:
            reasons.append(rejection)
        else:
            optional_gaps.append(rejection)

    return EligibilityResult(
        request=request.name,
        bed=bed.name,
        eligible=not reasons,
        reasons=reasons,
        matched_requirements=list(dict.fromkeys(matched)),
        optional_gaps=optional_gaps,
    )
