from __future__ import annotations

import calendar
import html
import logging
import re
import time
import xml.etree.ElementTree as ElementTree
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from .models import Article
from .state import article_id, canonical_url


LOG = logging.getLogger(__name__)
USER_AGENT = "AI-Insights-Update/2.0 (+https://github.com/12YHY21/AI-Insights-Update)"


@dataclass(frozen=True, slots=True)
class SourceHealth:
    name: str
    status: str
    item_count: int
    elapsed_ms: int
    newest_at: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    articles: list[Article]
    successful_sources: int
    failed_sources: int
    sources: list[SourceHealth]


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


def parse_sitemap_date(value: str, url: str, date_regex: str = "") -> datetime | None:
    if value:
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    if date_regex:
        match = re.search(date_regex, url)
        if match:
            raw = match.group(1)
            try:
                format_string = "%y%m%d" if len(raw) == 6 else "%Y%m%d"
                return datetime.strptime(raw, format_string).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def collect_feeds(
    feeds: list[dict[str, Any]],
    cutoff: datetime,
    timeout_seconds: int,
    max_candidates: int,
    max_entries_per_feed: int = 30,
) -> CollectionResult:
    """Collect sources independently, then interleave them to preserve diversity."""

    by_id: dict[str, Article] = {}
    health: list[SourceHealth] = []
    successful_sources = 0
    failed_sources = 0
    workers = max(1, min(6, len(feeds)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="source") as executor:
        futures = {}
        for feed in feeds:
            started = time.perf_counter()
            future = executor.submit(
                _collect_one_source,
                feed,
                cutoff,
                timeout_seconds,
                max_entries_per_feed,
            )
            futures[future] = (feed, started)
        for future in as_completed(futures):
            feed, started = futures[future]
            try:
                source_items, elapsed_ms = future.result()
                successful_sources += 1
                status = "ok" if source_items else "empty"
                newest_at = source_items[0].published_at.isoformat() if source_items else ""
                health.append(
                    SourceHealth(
                        name=str(feed["name"]),
                        status=status,
                        item_count=len(source_items),
                        elapsed_ms=elapsed_ms,
                        newest_at=newest_at,
                    )
                )
                for candidate in source_items:
                    previous = by_id.get(candidate.id)
                    if previous is None or _article_quality(candidate) > _article_quality(previous):
                        by_id[candidate.id] = candidate
                if source_items:
                    LOG.info("信息源采集成功 [%s]：%d 条", feed["name"], len(source_items))
                else:
                    LOG.warning("信息源返回空结果 [%s]", feed["name"])
            except Exception as exc:  # One broken source must not stop the digest.
                failed_sources += 1
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                health.append(
                    SourceHealth(
                        name=str(feed.get("name", feed.get("url", "unknown"))),
                        status="failed",
                        item_count=0,
                        elapsed_ms=elapsed_ms,
                        error=f"{type(exc).__name__}: {str(exc)[:240]}",
                    )
                )
                LOG.warning("信息源采集失败 [%s]：%s", feed.get("name", feed.get("url")), exc)

    health.sort(key=lambda item: item.name.casefold())
    return CollectionResult(
        articles=_interleave_sources(list(by_id.values()), max_candidates),
        successful_sources=successful_sources,
        failed_sources=failed_sources,
        sources=health,
    )


def _collect_one_source(
    source: dict[str, Any],
    cutoff: datetime,
    timeout_seconds: int,
    max_entries: int,
) -> tuple[list[Article], int]:
    started = time.perf_counter()
    kind = str(source.get("kind", "rss")).lower()
    if kind == "rss":
        items = _collect_rss(source, cutoff, timeout_seconds, max_entries)
    elif kind == "sitemap":
        items = _collect_sitemap(source, cutoff, timeout_seconds, max_entries)
    else:
        raise ValueError(f"不支持的信息源类型：{kind}")
    return items, int((time.perf_counter() - started) * 1000)


def _collect_rss(
    source: dict[str, Any],
    cutoff: datetime,
    timeout_seconds: int,
    max_entries: int,
) -> list[Article]:
    now = datetime.now(timezone.utc)
    response = _get(str(source["url"]), timeout_seconds)
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
        items.append(_article_from_source(source, title, url, published_at, summary))
    items.sort(key=lambda item: item.published_at, reverse=True)
    return items[:max_entries]


def _collect_sitemap(
    source: dict[str, Any],
    cutoff: datetime,
    timeout_seconds: int,
    max_entries: int,
) -> list[Article]:
    response = _get(str(source["url"]), timeout_seconds)
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"站点地图 XML 无效：{exc}") from exc
    include_patterns = [str(value) for value in source.get("include_url_patterns", [])]
    dated_urls: list[tuple[datetime, str]] = []
    for node in root.findall(".//{*}url"):
        location = node.find("{*}loc")
        if location is None or not location.text:
            continue
        url = canonical_url(location.text.strip())
        if not url or (include_patterns and not any(pattern in url for pattern in include_patterns)):
            continue
        lastmod = node.find("{*}lastmod")
        published_at = parse_sitemap_date(
            lastmod.text.strip() if lastmod is not None and lastmod.text else "",
            url,
            str(source.get("date_from_url_regex", "")),
        )
        if published_at and published_at >= cutoff:
            dated_urls.append((published_at, url))

    dated_urls.sort(reverse=True)
    items: list[Article] = []
    for published_at, url in dated_urls[:max_entries]:
        try:
            page = _get(url, timeout_seconds)
            title, summary = _page_metadata(page.text, url)
            if title:
                items.append(_article_from_source(source, title, url, published_at, summary))
        except Exception as exc:
            LOG.info("站点地图页面提取失败 [%s]：%s", url, exc)
    return items


def enrich_full_text(article: Article, client: httpx.Client, max_characters: int) -> None:
    """Extract selected articles, preferring arXiv HTML over an abstract landing page."""

    urls = [article.url]
    arxiv_html = _arxiv_html_url(article.url)
    if arxiv_html:
        urls.insert(0, arxiv_html)

    best_text = article.summary
    resources: list[str] = []
    for url in urls:
        try:
            response = client.get(url)
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", "").lower():
                continue
            text, found_resources = _extract_article_text(response.text, article.url)
            if len(text) > len(best_text):
                best_text = text
            resources.extend(found_resources)
            if arxiv_html and url == arxiv_html and len(text) >= 2500:
                break
        except Exception as exc:
            LOG.info("正文候选提取失败 [%s]：%s", url, exc)

    article.content = best_text[:max_characters]
    article.resource_urls = list(dict.fromkeys(resources))[:5]


def _extract_article_text(page_html: str, original_url: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(page_html, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        node.decompose()
    main = soup.find("article") or soup.find("main") or soup.body
    text = clean_text(str(main)) if main else ""
    resources: list[str] = []
    if main:
        for link in main.find_all("a", href=True):
            url = canonical_url(str(link["href"]))
            host = urlsplit(url).netloc.lower() if url else ""
            if url != original_url and host in {"github.com", "huggingface.co", "paperswithcode.com"}:
                resources.append(url)
    return text, resources


def _page_metadata(page_html: str, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(page_html, "html.parser")
    title_meta = soup.find("meta", property="og:title")
    title = clean_text(str(title_meta.get("content", ""))) if title_meta else ""
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))
    description = ""
    for attributes in ({"name": "description"}, {"property": "og:description"}):
        node = soup.find("meta", attrs=attributes)
        if node and node.get("content"):
            description = clean_text(str(node["content"]))
            break
    title = re.sub(r"\s*[|\\-]\s*(Anthropic|DeepSeek).*$", "", title).strip()
    return title or url, description


def _get(url: str, timeout_seconds: int) -> httpx.Response:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html;q=0.9, */*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = httpx.get(url, timeout=timeout_seconds, follow_redirects=True, headers=headers)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                LOG.info("请求失败，准备重试 %d/3 [%s]：%s", attempt, url, exc)
                time.sleep(attempt)
    raise RuntimeError(f"请求连续三次失败：{url}") from last_error


def _article_from_source(
    source: dict[str, Any],
    title: str,
    url: str,
    published_at: datetime,
    summary: str,
) -> Article:
    return Article(
        id=article_id(url, title),
        title=title,
        url=url,
        source=str(source["name"]),
        source_category=str(source.get("category", "其他")),
        published_at=published_at,
        summary=summary[:8000],
    )


def _arxiv_html_url(url: str) -> str:
    match = re.match(r"https?://(?:www\.)?arxiv\.org/abs/([^/?#]+)", url)
    return f"https://arxiv.org/html/{match.group(1)}" if match else ""


def _article_quality(article: Article) -> tuple[int, int]:
    category_priority = {"官方动态": 4, "论文": 3, "工程与部署": 2, "开源与工程": 2}
    return category_priority.get(article.source_category, 1), len(article.summary)


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
