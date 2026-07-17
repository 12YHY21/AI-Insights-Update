import time
import unittest
from datetime import datetime, timezone

from src.collectors import clean_text, parse_entry_date


class CollectorTests(unittest.TestCase):
    def test_clean_text_removes_html_and_collapses_whitespace(self):
        self.assertEqual(clean_text("<p>Hello&nbsp; <b>world</b></p>"), "Hello world")

    def test_parse_entry_date_is_utc(self):
        parsed = parse_entry_date(
            {"published_parsed": time.struct_time((2026, 7, 17, 1, 2, 3, 4, 198, 0))},
            datetime.now(timezone.utc),
        )
        self.assertEqual(parsed, datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()

