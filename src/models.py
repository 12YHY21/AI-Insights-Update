from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Article:
    id: str
    title: str
    url: str
    source: str
    source_category: str
    published_at: datetime
    summary: str
    content: str = ""
    score: float = 0.0
    ai_category: str = "其他"
    ranking_reason: str = ""
    duplicate_of: str = ""
    resource_urls: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ArticleSummary:
    article: Article
    chinese_title: str
    one_liner: str
    problem: str
    approach: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    audience: str = ""


@dataclass(slots=True)
class MonthlyReviewItem:
    article: Article
    was_previously_sent: bool
    importance_score: float
    verdict: str
    reassessment: str
    latest_context: str
    recommendation: str


@dataclass(slots=True)
class MonthlyReviewDigest:
    period_label: str
    executive_summary: str
    themes: list[str]
    reviews: list[MonthlyReviewItem]
    top_ids: list[str]
    major_news_ids: list[str]
    watchlist: list[str]
