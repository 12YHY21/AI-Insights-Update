import unittest
from datetime import datetime, timezone

from src.main import _monthly_period


class MonthlyPeriodTests(unittest.TestCase):
    def test_previous_calendar_month(self):
        now = datetime(2026, 8, 1, 1, 17, tzinfo=timezone.utc)
        start, end, label = _monthly_period(now, "Asia/Shanghai", False)
        self.assertEqual(start, datetime(2026, 6, 30, 16, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 7, 31, 16, tzinfo=timezone.utc))
        self.assertEqual(label, "2026年07月")

    def test_current_month_preview(self):
        now = datetime(2026, 7, 17, 4, tzinfo=timezone.utc)
        start, end, label = _monthly_period(now, "Asia/Shanghai", True)
        self.assertEqual(start, datetime(2026, 6, 30, 16, tzinfo=timezone.utc))
        self.assertEqual(end, now)
        self.assertIn("截至07月17日", label)


if __name__ == "__main__":
    unittest.main()
