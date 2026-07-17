import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.models import Article
from src.state import StateStore, article_id, canonical_url


class StateTests(unittest.TestCase):
    def test_canonical_url_removes_tracking(self):
        self.assertEqual(
            canonical_url("HTTPS://Example.com/post/?utm_source=x&a=1#top"),
            "https://example.com/post?a=1",
        )

    def test_canonical_url_rejects_non_web_scheme(self):
        self.assertEqual(canonical_url("javascript:alert(1)"), "")

    def test_mark_and_reload_records_selected_and_unselected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            selected = self._article("https://example.com/a", "A", now)
            unselected = self._article("https://example.com/b", "B", now)
            store.mark_success([selected, unselected], {selected.id}, now)
            store.save()

            loaded = StateStore(path)
            self.assertTrue(loaded.has_seen(selected.id))
            self.assertTrue(loaded.has_seen(unselected.id))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data["items"][selected.id]["selected"])
            self.assertFalse(data["items"][unselected.id]["selected"])

    def test_cutoff_uses_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            now = datetime(2026, 7, 17, 8, tzinfo=timezone.utc)
            store = StateStore(path)
            store.mark_success([], set(), now)
            self.assertEqual(store.cutoff(7, 12, now), now - timedelta(hours=12))

    @staticmethod
    def _article(url: str, title: str, now: datetime) -> Article:
        return Article(
            id=article_id(url),
            title=title,
            url=url,
            source="test",
            source_category="test",
            published_at=now,
            summary="summary",
        )


if __name__ == "__main__":
    unittest.main()

