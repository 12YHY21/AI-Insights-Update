import unittest
from datetime import datetime, timezone

from src.ai import select_articles
from src.models import Article


class SelectionTests(unittest.TestCase):
    def test_selection_enforces_threshold_and_source_diversity(self):
        articles = [
            self._article("a1", "A", 9.5),
            self._article("a2", "A", 9.0),
            self._article("b1", "B", 8.5),
            self._article("c1", "C", 6.0),
        ]
        selected = select_articles(articles, 6.8, 3, 1)
        self.assertEqual([item.id for item in selected], ["a1", "b1"])

    @staticmethod
    def _article(item_id: str, source: str, score: float) -> Article:
        return Article(
            id=item_id,
            title=item_id,
            url=f"https://example.com/{item_id}",
            source=source,
            source_category="论文",
            published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
            summary="summary",
            score=score,
        )


if __name__ == "__main__":
    unittest.main()

