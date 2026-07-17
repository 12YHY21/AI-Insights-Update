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

