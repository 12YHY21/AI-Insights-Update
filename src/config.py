from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    settings: dict[str, Any]
    deepseek_api_key: str
    deepseek_base_url: str
    feishu_webhook_url: str
    feishu_webhook_secret: str
    rank_model: str
    summary_model: str


def load_config(require_ai: bool = True, require_feishu: bool = True) -> RuntimeConfig:
    """Load non-secret settings plus secrets supplied through environment variables."""

    load_dotenv(ROOT / ".env")
    settings_path = Path(os.getenv("DIGEST_SETTINGS_PATH", ROOT / "config" / "settings.json"))
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"配置文件不存在：{settings_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"配置文件不是有效 JSON：{settings_path}: {exc}") from exc
    _validate_settings(settings)

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    secret = os.getenv("FEISHU_WEBHOOK_SECRET", "").strip()

    missing: list[str] = []
    if require_ai and not api_key:
        missing.append("DEEPSEEK_API_KEY")
    if require_feishu and not webhook:
        missing.append("FEISHU_WEBHOOK_URL")
    if missing:
        raise RuntimeError(f"缺少环境变量：{', '.join(missing)}")

    return RuntimeConfig(
        settings=settings,
        deepseek_api_key=api_key,
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        feishu_webhook_url=webhook,
        feishu_webhook_secret=secret,
        rank_model=os.getenv("DEEPSEEK_RANK_MODEL", "deepseek-v4-flash"),
        summary_model=os.getenv("DEEPSEEK_SUMMARY_MODEL", "deepseek-v4-pro"),
    )


def _validate_settings(settings: dict[str, Any]) -> None:
    required = {
        "title",
        "timezone",
        "lookback_days_on_first_run",
        "max_candidates",
        "max_selected",
        "minimum_score",
        "feeds",
        "interests",
    }
    missing = sorted(required - settings.keys())
    if missing:
        raise RuntimeError(f"配置缺少字段：{', '.join(missing)}")
    if not isinstance(settings["feeds"], list) or not settings["feeds"]:
        raise RuntimeError("配置中的 feeds 必须是非空列表")
    names: set[str] = set()
    for index, feed in enumerate(settings["feeds"], 1):
        if not isinstance(feed, dict) or not feed.get("name") or not feed.get("url"):
            raise RuntimeError(f"第 {index} 个 feed 缺少 name 或 url")
        name = str(feed["name"])
        if name in names:
            raise RuntimeError(f"信息源名称重复：{name}")
        names.add(name)
        if not str(feed["url"]).startswith("https://"):
            raise RuntimeError(f"信息源必须使用 HTTPS：{name}")
        if str(feed.get("kind", "rss")).lower() not in {"rss", "sitemap"}:
            raise RuntimeError(f"信息源类型无效：{name}")

    monthly = settings.get("monthly_review", {})
    if not isinstance(monthly, dict):
        raise RuntimeError("monthly_review 必须是对象")
    positive_fields = {
        "max_history_items",
        "max_news_candidates",
        "max_news_items",
        "max_candidates",
        "max_entries_per_feed",
        "full_text_max_characters",
    }
    for field in positive_fields:
        if field in monthly and (
            not isinstance(monthly[field], int)
            or isinstance(monthly[field], bool)
            or monthly[field] <= 0
        ):
            raise RuntimeError(f"monthly_review.{field} 必须是正整数")
    minimum_score = monthly.get("minimum_news_score", 7.0)
    if not isinstance(minimum_score, (int, float)) or not 0 <= minimum_score <= 10:
        raise RuntimeError("monthly_review.minimum_news_score 必须在 0 到 10 之间")
    scan_categories = monthly.get("scan_categories", [])
    if not isinstance(scan_categories, list) or not all(
        isinstance(value, str) and value.strip() for value in scan_categories
    ):
        raise RuntimeError("monthly_review.scan_categories 必须是字符串列表")
    if monthly.get("max_news_items", 10) > monthly.get("max_news_candidates", 48):
        raise RuntimeError("monthly_review.max_news_items 不能大于 max_news_candidates")
