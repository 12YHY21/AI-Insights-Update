import unittest
from datetime import datetime, timezone

from src.ai import (
    _validate_monthly_review,
    _validate_rank_result,
    deduplicate_similar_titles,
    select_articles,
)
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

    def test_selection_reserves_categories_and_caps_papers(self):
        articles = [
            self._article("p1", "arXiv A", 9.8, "论文"),
            self._article("p2", "arXiv B", 9.7, "论文"),
            self._article("p3", "arXiv C", 9.6, "论文"),
            self._article("p4", "arXiv D", 9.5, "论文"),
            self._article("o1", "Official", 7.5, "官方动态"),
            self._article("e1", "Engineering", 7.2, "工程与部署"),
        ]
        selected = select_articles(
            articles,
            6.8,
            6,
            2,
            {"论文": 3, "官方动态": 2, "工程与部署": 2},
            {"官方动态": 1, "工程与部署": 1},
        )
        self.assertIn("o1", [item.id for item in selected])
        self.assertIn("e1", [item.id for item in selected])
        self.assertEqual(sum(item.source_category == "论文" for item in selected), 3)

    def test_similar_titles_are_deduplicated(self):
        first = self._article("a", "Official", 0, "官方动态")
        first.title = "Introducing Example Model 2.0"
        second = self._article("b", "Mirror", 0, "开源与工程")
        second.title = "Introducing Example Model 2.0!"
        unique, duplicates = deduplicate_similar_titles([first, second], 0.9)
        self.assertEqual([item.id for item in unique], ["a"])
        self.assertEqual([item.id for item in duplicates], ["b"])
        self.assertEqual(second.duplicate_of, "a")

    def test_rank_validation_rejects_missing_ids(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            _validate_rank_result(
                {"items": [{"id": "a", "score": 8, "category": "Agent", "reason": "good"}]},
                {"a", "b"},
                False,
            )

    def test_rerank_validation_rejects_duplicate_chain(self):
        result = {
            "items": [
                {"id": "a", "score": 8, "category": "Agent", "reason": "good", "duplicate_of": "b"},
                {"id": "b", "score": 8, "category": "Agent", "reason": "good", "duplicate_of": "c"},
                {"id": "c", "score": 9, "category": "Agent", "reason": "best", "duplicate_of": None},
            ]
        }
        with self.assertRaisesRegex(ValueError, "也被标记为重复"):
            _validate_rank_result(result, {"a", "b", "c"}, True)

    def test_monthly_validation_requires_new_major_news(self):
        result = {
            "executive_summary": "summary",
            "themes": ["theme"],
            "reviews": [
                {
                    "id": item_id,
                    "importance_score": 8,
                    "verdict": "仍属前沿",
                    "reassessment": "still important",
                    "latest_context": "confirmed",
                    "recommendation": "follow",
                }
                for item_id in ("old", "new")
            ],
            "top_ids": ["old", "new"],
            "major_news_ids": ["new"],
            "watchlist": ["next"],
        }
        _validate_monthly_review(result, {"old", "new"}, {"new"}, {"old"})
        result["major_news_ids"] = ["old"]
        with self.assertRaisesRegex(ValueError, "无效 id"):
            _validate_monthly_review(result, {"old", "new"}, {"new"}, {"old"})

    @staticmethod
    def _article(item_id: str, source: str, score: float, category: str = "论文") -> Article:
        return Article(
            id=item_id,
            title=item_id,
            url=f"https://example.com/{item_id}",
            source=source,
            source_category=category,
            published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
            summary="summary",
            score=score,
        )


if __name__ == "__main__":
    unittest.main()
