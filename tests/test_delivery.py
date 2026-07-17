import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.main import _deliver_pending
from src.models import Article
from src.state import StateStore, article_id


class FakeSender:
    def __init__(self):
        self.calls = []

    def send_markdown_card(self, title, markdown, sequence=""):
        self.calls.append((title, markdown, sequence))


class DeliveryTests(unittest.TestCase):
    def test_resume_sends_only_remaining_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            now = datetime(2026, 7, 17, tzinfo=timezone.utc)
            article = Article(
                id=article_id("https://example.com/a"),
                title="A",
                url="https://example.com/a",
                source="test",
                source_category="test",
                published_at=now,
                summary="summary",
            )
            state = StateStore(path)
            state.start_delivery(
                "digest1",
                "title",
                "markdown",
                ["part1", "part2"],
                [article],
                {article.id},
                now,
                "2026-07-17",
            )
            state.mark_delivery_chunk_sent(0)
            state.save()

            sender = FakeSender()
            snapshot = _deliver_pending(sender, StateStore(path), now)
            self.assertEqual(sender.calls, [("title", "part2", "（2/2）")])
            self.assertEqual(snapshot["id"], "digest1")
            self.assertIsNone(StateStore(path).pending_delivery)


if __name__ == "__main__":
    unittest.main()
