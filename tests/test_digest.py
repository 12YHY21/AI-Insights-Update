import unittest
from datetime import datetime, timezone

from src.digest import render_markdown, render_monthly_markdown, split_for_feishu
from src.models import Article, ArticleSummary, MonthlyReviewDigest, MonthlyReviewItem


class DigestTests(unittest.TestCase):
    def test_split_keeps_small_digest(self):
        self.assertEqual(split_for_feishu("hello", 100), ["hello"])

    def test_split_on_section_boundary(self):
        value = "first section\n\n---\n\nsecond section"
        self.assertEqual(split_for_feishu(value, 100), [value])

    def test_split_enforces_hard_limit(self):
        chunks = split_for_feishu("x" * 350, 100)
        self.assertEqual(len(chunks), 4)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))

    def test_render_contains_source_and_link(self):
        now = datetime(2026, 7, 17, tzinfo=timezone.utc)
        article = Article(
            id="1",
            title="Original title",
            url="https://example.com/article",
            source="Example",
            source_category="论文",
            published_at=now,
            summary="Summary",
            score=8.5,
            ai_category="推理优化",
            ranking_reason="技术路线新颖",
        )
        summary = ArticleSummary(
            article=article,
            chinese_title="中文标题",
            one_liner="一句话",
            problem="问题",
            approach=["方法"],
            findings=["结果"],
            limitations=["局限"],
            audience="工程师",
        )
        article.resource_urls = ["https://github.com/example/project"]
        rendered = render_markdown([summary], 8, "Asia/Shanghai", now, "digest123")
        self.assertIn("中文标题", rendered)
        self.assertIn("Original title", rendered)
        self.assertIn("https://example.com/article", rendered)
        self.assertIn("digest123", rendered)
        self.assertIn("https://github.com/example/project", rendered)

    def test_render_monthly_review_contains_reassessment_sections(self):
        now = datetime(2026, 7, 17, tzinfo=timezone.utc)
        article = Article(
            id="a",
            title="Major Model Release",
            url="https://example.com/model",
            source="Official",
            source_category="官方动态",
            published_at=now,
            summary="summary",
        )
        item = MonthlyReviewItem(
            article=article,
            was_previously_sent=True,
            importance_score=9.2,
            verdict="仍属前沿",
            reassessment="重新阅读后依然重要",
            latest_context="后续新闻确认其影响",
            recommendation="继续跟踪",
        )
        digest = MonthlyReviewDigest(
            period_label="2026年07月",
            executive_summary="模型能力显著前进",
            themes=["推理能力"],
            reviews=[item],
            top_ids=["a"],
            major_news_ids=[],
            watchlist=["观察实际采用"],
        )
        rendered = render_monthly_markdown(digest, 12, "Asia/Shanghai", now, "month1")
        self.assertIn("AI 前沿月度复盘", rendered)
        self.assertIn("此前推送内容复核清单", rendered)
        self.assertIn("重新阅读后依然重要", rendered)


if __name__ == "__main__":
    unittest.main()
