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
    for index, feed in enumerate(settings["feeds"], 1):
        if not isinstance(feed, dict) or not feed.get("name") or not feed.get("url"):
            raise RuntimeError(f"第 {index} 个 feed 缺少 name 或 url")

