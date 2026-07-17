from __future__ import annotations

import calendar
import html
import logging
import re
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup

from .models import Article
from .state import article_id, canonical_url


LOG = logging.getLogger(__name__)
USER_AGENT = "AI-Insights-Update/1.0 (+https://github.com/12YHY21/AI-Insights-Update)"


@dataclass(frozen=True, slots=True)
class CollectionResult:
    articles: list[Article]
    successful_sources: int
    failed_sources: int


def clean_text(value: str) -> str:
    unescaped = html.unescape(value or "")
    if "<" in unescaped and ">" in unescaped:
        unescaped = BeautifulSoup(unescaped, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", unescaped).strip()


def parse_entry_date(entry: dict[str, Any], fallback: datetime) -> datetime:
    struct_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct_time:
        return fallback.astimezone(timezone.utc)
    return datetime.fromtimestamp(calendar.timegm(struct_time), tz=timezone.utc)


def collect_feeds(
    feeds: list[dict[str, Any]],
    cutoff: datetime,
    timeout_seconds: int,
    max_candidates: int,
    max_entries_per_feed: int = 30,
) -> CollectionResult:
    """Collect feeds independently, then interleave sources to preserve diversity."""

    by_id: dict[str, Article] = {}
    successful_sources = 0
    failed_sources = 0
    workers = max(1, min(6, len(feeds)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="feed") as executor:
        futures = {
            executor.submit(
                _collect_one_feed,
                feed,
                cutoff,
                timeout_seconds,
                max_entries_per_feed,
            ): feed
            for feed in feeds
        }
        for future in as_completed(futures):
            feed = futures[future]
            try:
                source_items = future.result()
                successful_sources += 1
                for candidate in source_items:
                    previous = by_id.get(candidate.id)
                    if previous is None or len(candidate.summary) > len(previous.summary):
                        by_id[candidate.id] = candidate
                LOG.info("信息源采集成功 [%s]：%d 条", feed["name"], len(source_items))
            except Exception as exc:  # One broken source must not stop the digest.
                failed_sources += 1
                LOG.warning("信息源采集失败 [%s]：%s", feed.get("name", feed.get("url")), exc)

    return CollectionResult(
        articles=_interleave_sources(list(by_id.values()), max_candidates),
        successful_sources=successful_sources,
        failed_sources=failed_sources,
    )


def _collect_one_feed(
    feed: dict[str, Any],
    cutoff: datetime,
    timeout_seconds: int,
    max_entries: int,
) -> list[Article]:
    now = datetime.now(timezone.utc)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, text/xml;q=0.9, */*;q=0.5",
        # Some CDN-generated RSS responses advertise Brotli but close with an invalid final frame.
        "Accept-Encoding": "gzip, deflate",
    }
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
        response = client.get(str(feed["url"]))
        response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(str(parsed.bozo_exception))

    items: list[Article] = []
    for entry in parsed.entries:
        url = canonical_url(str(entry.get("link", "")))
        title = clean_text(str(entry.get("title", "")))
        if not url or not title:
            continue
        published_at = parse_entry_date(entry, now)
        if published_at < cutoff:
            continue
        summary = clean_text(str(entry.get("summary", "") or entry.get("description", "")))
        items.append(
            Article(
                id=article_id(url, title),
                title=title,
                url=url,
                source=str(feed["name"]),
                source_category=str(feed.get("category", "其他")),
                published_at=published_at,
                summary=summary[:8000],
            )
        )
    items.sort(key=lambda item: item.published_at, reverse=True)
    return items[:max_entries]


def _interleave_sources(articles: list[Article], limit: int) -> list[Article]:
    groups: dict[str, deque[Article]] = defaultdict(deque)
    for article in sorted(articles, key=lambda item: item.published_at, reverse=True):
        groups[article.source].append(article)

    result: list[Article] = []
    while groups and len(result) < limit:
        source_order = sorted(
            groups,
            key=lambda source: groups[source][0].published_at,
            reverse=True,
        )
        for source in source_order:
            result.append(groups[source].popleft())
            if not groups[source]:
                del groups[source]
            if len(result) >= limit:
                break
    return result


def enrich_full_text(article: Article, client: httpx.Client, max_characters: int) -> None:
    """Best-effort extraction; always retain the feed summary as a safe fallback."""

    if len(article.summary) >= 1600:
        article.content = article.summary
        return
    try:
        response = client.get(article.url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type:
            article.content = article.summary
            return
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            node.decompose()
        main = soup.find("article") or soup.find("main") or soup.body
        text = clean_text(str(main)) if main else ""
        article.content = text[:max_characters] if len(text) > len(article.summary) else article.summary
    except Exception as exc:
        LOG.info("正文提取失败，回退到摘要 [%s]：%s", article.url, exc)
        article.content = article.summary
