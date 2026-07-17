import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.models import Article
from src.state import StateStore, article_id, canonical_url, digest_id


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

    def test_pending_delivery_can_resume_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            article = self._article("https://example.com/a", "A", now)
            store = StateStore(path)
            store.start_delivery(
                "digest1",
                "title",
                "markdown",
                ["part1", "part2"],
                [article],
                {article.id},
                now,
                "2026-07-17",
            )
            store.mark_delivery_chunk_sent(0)
            store.save()

            resumed = StateStore(path)
            self.assertEqual(resumed.pending_delivery["sent_chunk_indexes"], [0])
            resumed.mark_delivery_chunk_sent(1)
            snapshot = resumed.complete_delivery(now)
            self.assertEqual(snapshot["id"], "digest1")
            self.assertIsNone(resumed.pending_delivery)
            self.assertTrue(resumed.has_seen(article.id))

    def test_digest_id_is_order_independent(self):
        self.assertEqual(digest_id(["b", "a"], "2026-07-17"), digest_id(["a", "b"], "2026-07-17"))

    def test_invalid_pending_delivery_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"version": 2, "items": {}, "pending_delivery": {"id": "broken"}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "缺少字段"):
                StateStore(path)

    def test_monthly_delivery_does_not_advance_weekly_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            weekly_at = datetime(2026, 7, 6, tzinfo=timezone.utc)
            monthly_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
            weekly = self._article("https://example.com/weekly", "Weekly", weekly_at)
            monthly = self._article("https://example.com/monthly", "Monthly", monthly_at)
            store = StateStore(path)
            store.mark_success([weekly], {weekly.id}, weekly_at)
            store.start_delivery(
                "monthly1",
                "monthly",
                "markdown",
                ["part"],
                [monthly],
                {monthly.id},
                monthly_at,
                "2026-08-01",
                delivery_kind="monthly",
                updates_last_success=False,
            )
            store.mark_delivery_chunk_sent(0)
            store.complete_delivery(monthly_at)
            self.assertEqual(store.data["last_success_at"], weekly_at.isoformat())
            self.assertEqual(store.data["last_monthly_success_at"], monthly_at.isoformat())
            selected = store.selected_items_between(
                weekly_at - timedelta(days=1),
                weekly_at + timedelta(days=1),
            )
            self.assertEqual([item["id"] for item in selected], [weekly.id])

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
