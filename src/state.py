from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"状态文件损坏：{path}: {exc}") from exc
        else:
            self.data = {"version": 1, "last_success_at": None, "items": {}}
        self.data.setdefault("version", 1)
        self.data.setdefault("last_success_at", None)
        self.data.setdefault("items", {})

    def cutoff(self, first_run_lookback_days: int, overlap_hours: int, now: datetime) -> datetime:
        value = self.data.get("last_success_at")
        if value:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) - timedelta(hours=overlap_hours)
        return now.astimezone(timezone.utc) - timedelta(days=first_run_lookback_days)

    def has_seen(self, item_id: str) -> bool:
        return item_id in self.data.get("items", {})

    def mark_success(
        self,
        processed_articles: list[Article],
        selected_ids: set[str],
        now: datetime,
    ) -> None:
        items = self.data.setdefault("items", {})
        pushed_at = now.astimezone(timezone.utc).isoformat()
        for article in processed_articles:
            items[article.id] = {
                "title": article.title,
                "url": article.url,
                "source": article.source,
                "selected": article.id in selected_ids,
                "processed_at": pushed_at,
            }
        self.data["last_success_at"] = pushed_at
        self._prune(now)

    def _prune(self, now: datetime, keep_days: int = 180) -> None:
        threshold = now.astimezone(timezone.utc) - timedelta(days=keep_days)
        kept: dict[str, dict] = {}
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

