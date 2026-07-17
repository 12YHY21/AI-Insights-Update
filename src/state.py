from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Article


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query) if key.lower() not in TRACKING_PARAMS]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def article_id(url: str, title: str = "") -> str:
    basis = canonical_url(url) if url else title.strip().casefold()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def digest_id(article_ids: list[str], edition_date: str) -> str:
    basis = f"{edition_date}|{'|'.join(sorted(article_ids))}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"状态文件损坏：{path}: {exc}") from exc
        else:
            self.data = {
                "version": 2,
                "last_success_at": None,
                "pending_delivery": None,
                "items": {},
            }
        self.data["version"] = 2
        self.data.setdefault("last_success_at", None)
        self.data.setdefault("pending_delivery", None)
        self.data.setdefault("items", {})
        if not isinstance(self.data["items"], dict):
            raise RuntimeError(f"状态文件 items 字段无效：{path}")
        self._validate_pending_delivery()

    @property
    def pending_delivery(self) -> dict[str, Any] | None:
        value = self.data.get("pending_delivery")
        return value if isinstance(value, dict) else None

    def cutoff(self, first_run_lookback_days: int, overlap_hours: int, now: datetime) -> datetime:
        value = self.data.get("last_success_at")
        if value:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) - timedelta(hours=overlap_hours)
        return now.astimezone(timezone.utc) - timedelta(days=first_run_lookback_days)

    def has_seen(self, item_id: str) -> bool:
        return item_id in self.data.get("items", {})

    def start_delivery(
        self,
        delivery_id: str,
        title: str,
        markdown: str,
        chunks: list[str],
        processed_articles: list[Article],
        selected_ids: set[str],
        success_at: datetime,
        edition_date: str,
        archive: bool = True,
    ) -> None:
        existing = self.pending_delivery
        if existing:
            if existing.get("id") == delivery_id:
                return
            raise RuntimeError(f"仍有未完成投递：{existing.get('id', 'unknown')}")
        if not chunks:
            raise ValueError("投递内容不能为空")
        self.data["pending_delivery"] = {
            "id": delivery_id,
            "title": title,
            "markdown": markdown,
            "chunks": chunks,
            "sent_chunk_indexes": [],
            "processed_items": [self._serialize_article(item) for item in processed_articles],
            "selected_ids": sorted(selected_ids),
            "success_at": success_at.astimezone(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "edition_date": edition_date,
            "archive": archive,
        }

    def mark_delivery_chunk_sent(self, index: int) -> None:
        pending = self.pending_delivery
        if not pending:
            raise RuntimeError("没有待投递内容")
        if index < 0 or index >= len(pending.get("chunks", [])):
            raise IndexError("投递分片索引越界")
        sent = {int(value) for value in pending.get("sent_chunk_indexes", [])}
        sent.add(index)
        pending["sent_chunk_indexes"] = sorted(sent)

    def complete_delivery(self, completed_at: datetime) -> dict[str, Any]:
        pending = self.pending_delivery
        if not pending:
            raise RuntimeError("没有待完成投递")
        chunks = pending.get("chunks", [])
        sent = {int(value) for value in pending.get("sent_chunk_indexes", [])}
        if sent != set(range(len(chunks))):
            raise RuntimeError("仍有飞书分片未成功投递")

        items = self.data.setdefault("items", {})
        selected_ids = set(pending.get("selected_ids", []))
        processed_at = completed_at.astimezone(timezone.utc).isoformat()
        for item in pending.get("processed_items", []):
            item_id = str(item.get("id", ""))
            if not item_id:
                continue
            items[item_id] = {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "selected": item_id in selected_ids,
                "processed_at": processed_at,
            }
        self.data["last_success_at"] = pending.get("success_at", processed_at)
        snapshot = copy.deepcopy(pending)
        self.data["pending_delivery"] = None
        self._prune(completed_at)
        return snapshot

    def mark_success(
        self,
        processed_articles: list[Article],
        selected_ids: set[str],
        now: datetime,
    ) -> None:
        items = self.data.setdefault("items", {})
        processed_at = now.astimezone(timezone.utc).isoformat()
        for article in processed_articles:
            items[article.id] = {
                "title": article.title,
                "url": article.url,
                "source": article.source,
                "selected": article.id in selected_ids,
                "processed_at": processed_at,
            }
        self.data["last_success_at"] = processed_at
        self._prune(now)

    def _prune(self, now: datetime, keep_days: int = 180) -> None:
        threshold = now.astimezone(timezone.utc) - timedelta(days=keep_days)
        kept: dict[str, dict[str, Any]] = {}
        for key, value in self.data.get("items", {}).items():
            try:
                processed_at = datetime.fromisoformat(str(value["processed_at"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                continue
            if processed_at >= threshold:
                kept[key] = value
        self.data["items"] = kept

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _validate_pending_delivery(self) -> None:
        pending = self.data.get("pending_delivery")
        if pending is None:
            return
        if not isinstance(pending, dict):
            raise RuntimeError(f"状态文件 pending_delivery 字段无效：{self.path}")
        required = {"id", "title", "markdown", "chunks", "sent_chunk_indexes", "edition_date"}
        missing = sorted(required - pending.keys())
        if missing:
            raise RuntimeError(f"未完成投递缺少字段：{', '.join(missing)}")
        chunks = pending.get("chunks")
        if not isinstance(chunks, list) or not chunks or not all(isinstance(value, str) for value in chunks):
            raise RuntimeError("未完成投递的 chunks 字段无效")
        sent = pending.get("sent_chunk_indexes")
        if not isinstance(sent, list) or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 or value >= len(chunks)
            for value in sent
        ):
            raise RuntimeError("未完成投递的 sent_chunk_indexes 字段无效")
        if len(sent) != len(set(sent)):
            raise RuntimeError("未完成投递的 sent_chunk_indexes 包含重复项")

    @staticmethod
    def _serialize_article(article: Article) -> dict[str, str]:
        return {
            "id": article.id,
            "title": article.title,
            "url": article.url,
            "source": article.source,
        }
