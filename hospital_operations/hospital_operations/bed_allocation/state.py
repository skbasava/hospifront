"""Operational bed lifecycle.

This module is intentionally free of Frappe imports so the transition rules can
be unit-tested without a site. Clinical authority for occupancy remains
``Inpatient Occupancy``; this state machine only describes operational readiness.
"""

READY = "READY"
RESERVED = "RESERVED"
OCCUPIED = "OCCUPIED"
VACATED = "VACATED"
CLEANING_REQUIRED = "CLEANING_REQUIRED"
CLEANING_IN_PROGRESS = "CLEANING_IN_PROGRESS"
ISOLATION_CLEANING = "ISOLATION_CLEANING"
MAINTENANCE = "MAINTENANCE"
BLOCKED = "BLOCKED"

STATES = (
    READY,
    RESERVED,
    OCCUPIED,
    VACATED,
    CLEANING_REQUIRED,
    CLEANING_IN_PROGRESS,
    ISOLATION_CLEANING,
    MAINTENANCE,
    BLOCKED,
)

CLEANING_STATES = frozenset(
    {CLEANING_REQUIRED, CLEANING_IN_PROGRESS, ISOLATION_CLEANING}
)
UNAVAILABLE_STATES = CLEANING_STATES | frozenset({MAINTENANCE, BLOCKED})
ALLOCATABLE_STATES = frozenset({READY})

TRANSITIONS = {
    READY: frozenset({RESERVED, OCCUPIED, MAINTENANCE, BLOCKED}),
    RESERVED: frozenset({OCCUPIED, READY, BLOCKED}),
    OCCUPIED: frozenset({VACATED}),
    VACATED: frozenset(
        {
            CLEANING_REQUIRED,
            CLEANING_IN_PROGRESS,
            ISOLATION_CLEANING,
            MAINTENANCE,
            BLOCKED,
        }
    ),
    CLEANING_REQUIRED: frozenset(
        {CLEANING_IN_PROGRESS, ISOLATION_CLEANING, MAINTENANCE, BLOCKED}
    ),
    CLEANING_IN_PROGRESS: frozenset({READY, MAINTENANCE, BLOCKED}),
    ISOLATION_CLEANING: frozenset({READY, MAINTENANCE, BLOCKED}),
    MAINTENANCE: frozenset({READY, BLOCKED}),
    BLOCKED: frozenset({READY, MAINTENANCE}),
}


class InvalidBedTransition(ValueError):
    pass


def can_transition(current, new):
    if current not in STATES or new not in STATES:
        return False
    return new in TRANSITIONS[current]


def assert_transition(current, new):
    if not can_transition(current, new):
        raise InvalidBedTransition(
            f"Invalid bed state transition: {current!r} -> {new!r}"
        )
    return True


def is_allocatable(state):
    return state in ALLOCATABLE_STATES


def requires_cleaning(state):
    return state in CLEANING_STATES or state == VACATED
