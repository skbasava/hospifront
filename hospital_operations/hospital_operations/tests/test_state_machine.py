import unittest

from hospital_operations.bed_allocation import state


class TestBedStateMachine(unittest.TestCase):
    def test_happy_path(self):
        path = [
            state.READY,
            state.RESERVED,
            state.OCCUPIED,
            state.VACATED,
            state.CLEANING_REQUIRED,
            state.CLEANING_IN_PROGRESS,
            state.READY,
        ]
        for current, new in zip(path, path[1:]):
            self.assertTrue(state.can_transition(current, new))

    def test_occupied_cannot_become_ready_directly(self):
        self.assertFalse(state.can_transition(state.OCCUPIED, state.READY))
        with self.assertRaises(state.InvalidBedTransition):
            state.assert_transition(state.OCCUPIED, state.READY)

    def test_vacated_requires_cleaning(self):
        self.assertFalse(state.can_transition(state.VACATED, state.READY))
        self.assertTrue(state.requires_cleaning(state.VACATED))

    def test_isolation_cleaning_returns_to_ready(self):
        self.assertTrue(
            state.can_transition(state.ISOLATION_CLEANING, state.READY)
        )

    def test_maintenance_and_blocked_paths(self):
        self.assertTrue(state.can_transition(state.READY, state.MAINTENANCE))
        self.assertTrue(state.can_transition(state.MAINTENANCE, state.READY))
        self.assertTrue(state.can_transition(state.BLOCKED, state.MAINTENANCE))

    def test_unknown_states_are_rejected(self):
        self.assertFalse(state.can_transition("NOT_A_STATE", state.READY))
        self.assertFalse(state.can_transition(state.READY, "NOT_A_STATE"))

    def test_only_ready_is_allocatable(self):
        self.assertTrue(state.is_allocatable(state.READY))
        for value in state.STATES:
            if value != state.READY:
                self.assertFalse(state.is_allocatable(value))


if __name__ == "__main__":
    unittest.main()
