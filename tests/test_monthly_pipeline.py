import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.collectors import CollectionResult, SourceHealth
from src.config import load_config
from src.main import _run_monthly
from src.models import Article, MonthlyReviewDigest, MonthlyReviewItem
from src.state import StateStore, article_id


class FakeMonthlyEditor:
    def __init__(self, *_args, **_kwargs):
        pass

    def rank(self, articles, _interests, _chunk_size):
        for article in articles:
            article.score = 8.5
            article.ranking_reason = "major release"
        return articles

    def rerank(self, articles, _interests, _top_n, _per_category):
        return articles

    def monthly_review(self, articles, history_ids, period_label, _interests, _characters):
        reviews = [
            MonthlyReviewItem(
                article=article,
                was_previously_sent=article.id in history_ids,
                importance_score=9.0,
                verdict="仍属前沿",
                reassessment="仍然重要",
                latest_context="得到本月材料支持",
                recommendation="继续跟踪",
            )
            for article in articles
        ]
        return MonthlyReviewDigest(
            period_label=period_label,
            executive_summary="本月存在重要进展",
            themes=["基础模型升级"],
            reviews=reviews,
            top_ids=[article.id for article in articles],
            major_news_ids=[article.id for article in articles if article.id not in history_ids],
            watchlist=["观察真实采用"],
        )

    def usage_report(self):
        return {"models": {}, "total_requests": 0, "total_tokens": 0}


class MonthlyPipelineTests(unittest.TestCase):
    @patch("src.main._write_report")
    @patch("src.main._write_preview")
    @patch("src.main.enrich_articles")
    @patch("src.main.DeepSeekEditor", FakeMonthlyEditor)
    @patch("builtins.print")
    def test_monthly_dry_run_revisits_history_and_adds_news(
        self,
        _print,
        _enrich,
        write_preview,
        _write_report,
    ):
        now = datetime(2026, 7, 17, 4, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.json")
            history_at = now - timedelta(days=1)
            history = self._article("history", "Previously sent", "arXiv cs.AI", "论文", history_at)
            state.mark_success([history], {history.id}, history_at)
            news = self._article("news", "Major Model Release", "OpenAI", "官方动态", now - timedelta(hours=1))
            collection = CollectionResult(
                articles=[news],
                successful_sources=1,
                failed_sources=0,
                sources=[SourceHealth("OpenAI", "ok", 1, 10, now.isoformat())],
            )
            args = Namespace(
                collect_only=False,
                dry_run=True,
                monthly=True,
                current_month=True,
            )
            config = load_config(require_ai=False, require_feishu=False)
            with patch("src.main.collect_feeds", return_value=collection):
                result = _run_monthly(
                    args,
                    config,
                    state,
                    None,
                    now,
                    "Asia/Shanghai",
                    "2026-07-17",
                    True,
                )

            self.assertEqual(result, 0)
            markdown = write_preview.call_args.args[0]
            self.assertIn("Previously sent", markdown)
            self.assertIn("Major Model Release", markdown)
            self.assertIsNone(state.data["last_monthly_success_at"])

    @staticmethod
    def _article(item_id, title, source, category, now):
        url = f"https://example.com/{item_id}"
        return Article(
            id=article_id(url),
            title=title,
            url=url,
            source=source,
            source_category=category,
            published_at=now,
            summary="summary",
        )


if __name__ == "__main__":
    unittest.main()
