import unittest
from datetime import datetime, timedelta

from hospital_operations.bed_allocation.postop_windows import (
    ReservationWindow,
    WindowError,
    default_window,
    windows_overlap,
)


class TestPostOperativeWindows(unittest.TestCase):
    def test_rejects_window_without_positive_duration(self):
        start = datetime(2026, 11, 11, 8, 0)
        with self.assertRaises(WindowError):
            ReservationWindow(start, start)
        with self.assertRaises(WindowError):
            ReservationWindow(start, start - timedelta(minutes=1))

    def test_adjacent_windows_do_not_overlap(self):
        start = datetime(2026, 11, 11, 8, 0)
        end = datetime(2026, 11, 11, 10, 0)
        self.assertFalse(windows_overlap(start, end, end, end + timedelta(hours=2)))

    def test_partial_overlap_is_detected(self):
        start = datetime(2026, 11, 11, 8, 0)
        self.assertTrue(
            windows_overlap(
                start,
                start + timedelta(hours=4),
                start + timedelta(hours=2),
                start + timedelta(hours=6),
            )
        )

    def test_contained_window_is_detected(self):
        start = datetime(2026, 11, 11, 8, 0)
        self.assertTrue(
            windows_overlap(
                start,
                start + timedelta(hours=8),
                start + timedelta(hours=1),
                start + timedelta(hours=2),
            )
        )

    def test_disjoint_windows_do_not_overlap(self):
        start = datetime(2026, 11, 11, 8, 0)
        self.assertFalse(
            windows_overlap(
                start,
                start + timedelta(hours=2),
                start + timedelta(hours=3),
                start + timedelta(hours=5),
            )
        )

    def test_reservation_window_reports_overlap(self):
        start = datetime(2026, 11, 11, 8, 0)
        first = ReservationWindow(start, start + timedelta(hours=3))
        second = ReservationWindow(
            start + timedelta(hours=2), start + timedelta(hours=5)
        )
        self.assertTrue(first.overlaps(second))

    def test_default_window_starts_at_expected_surgery_end(self):
        expected_end = datetime(2026, 11, 11, 9, 30)
        window = default_window(expected_end, hold_hours=4)
        self.assertEqual(window.start_at, expected_end)
        self.assertEqual(window.end_at, expected_end + timedelta(hours=4))


if __name__ == "__main__":
    unittest.main()
