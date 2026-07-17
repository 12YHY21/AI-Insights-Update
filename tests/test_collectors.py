import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.collectors import _get, clean_text, parse_entry_date, parse_sitemap_date


class CollectorTests(unittest.TestCase):
    def test_clean_text_removes_html_and_collapses_whitespace(self):
        self.assertEqual(clean_text("<p>Hello&nbsp; <b>world</b></p>"), "Hello world")

    def test_parse_entry_date_is_utc(self):
        parsed = parse_entry_date(
            {"published_parsed": time.struct_time((2026, 7, 17, 1, 2, 3, 4, 198, 0))},
            datetime.now(timezone.utc),
        )
        self.assertEqual(parsed, datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc))

    def test_parse_sitemap_date_from_lastmod_and_url(self):
        self.assertEqual(
            parse_sitemap_date("2026-07-17T01:02:03Z", "https://example.com/news"),
            datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_sitemap_date("", "https://example.com/news260424", r"news(\d{6})"),
            datetime(2026, 4, 24, tzinfo=timezone.utc),
        )

    @patch("src.collectors.time.sleep")
    @patch("src.collectors.httpx.get")
    def test_get_retries_transient_failures(self, mock_get, _mock_sleep):
        response = MagicMock()
        response.raise_for_status.return_value = None
        mock_get.side_effect = [RuntimeError("temporary"), RuntimeError("temporary"), response]
        self.assertIs(_get("https://example.com/feed", 1), response)
        self.assertEqual(mock_get.call_count, 3)


if __name__ == "__main__":
    unittest.main()
