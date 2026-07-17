import unittest
from datetime import datetime, timezone

from src.digest import render_markdown, split_for_feishu
from src.models import Article, ArticleSummary


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
        rendered = render_markdown([summary], 8, "Asia/Shanghai", now)
        self.assertIn("中文标题", rendered)
        self.assertIn("Original title", rendered)
        self.assertIn("https://example.com/article", rendered)


if __name__ == "__main__":
    unittest.main()

