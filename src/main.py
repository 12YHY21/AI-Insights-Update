from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .ai import DeepSeekEditor, select_articles
from .collectors import USER_AGENT, collect_feeds, enrich_full_text
from .config import ROOT, load_config
from .digest import render_empty_markdown, render_markdown, split_for_feishu
from .feishu import FeishuSender
from .state import StateStore


LOG = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 前沿技术简报")
    parser.add_argument("--collect-only", action="store_true", help="只测试采集，不调用 AI、不推送")
    parser.add_argument("--dry-run", action="store_true", help="调用 AI 并生成预览，但不推送、不更新状态")
    parser.add_argument("--state-path", default=str(ROOT / "data" / "state.json"))
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    dry_run = args.dry_run or os.getenv("DIGEST_DRY_RUN", "").lower() in {"1", "true", "yes"}
    config = load_config(
        require_ai=not args.collect_only,
        require_feishu=not (args.collect_only or dry_run),
    )
    settings = config.settings
    now = datetime.now(timezone.utc)
    state = StateStore(Path(args.state_path))
    cutoff = state.cutoff(
        int(settings["lookback_days_on_first_run"]),
        int(settings.get("overlap_hours", 12)),
        now,
    )

    LOG.info("采集窗口开始于 %s", cutoff.isoformat())
    collection = collect_feeds(
        settings["feeds"],
        cutoff,
        int(settings.get("request_timeout_seconds", 25)),
        int(settings["max_candidates"]),
        int(settings.get("max_entries_per_feed", 30)),
    )
    if collection.successful_sources == 0:
        raise RuntimeError("所有信息源均采集失败，拒绝推进状态窗口")
    candidates = [item for item in collection.articles if not state.has_seen(item.id)]
    LOG.info(
        "获得 %d 条未处理候选（采集 %d 条；成功源 %d，失败源 %d）",
        len(candidates),
        len(collection.articles),
        collection.successful_sources,
        collection.failed_sources,
    )

    if args.collect_only:
        for item in candidates:
            print(f"{item.published_at.date()} | {item.source} | {item.title} | {item.url}")
        return 0

    sender = None
    if not dry_run:
        sender = FeishuSender(
            config.feishu_webhook_url,
            config.feishu_webhook_secret,
            int(settings.get("request_timeout_seconds", 25)),
        )

    if not candidates:
        markdown = render_empty_markdown(settings.get("timezone", "Asia/Shanghai"), now)
        _write_preview(markdown)
        if dry_run:
            print(markdown)
            return 0
        if settings.get("send_empty_digest", True):
            assert sender is not None
            sender.send_notice(settings["title"], markdown)
        state.mark_success([], set(), now)
        state.save()
        LOG.info("本期没有未处理的新内容，状态已更新")
        return 0

    editor = DeepSeekEditor(
        config.deepseek_api_key,
        config.deepseek_base_url,
        config.rank_model,
        config.summary_model,
    )
    ranked = editor.rank(
        candidates,
        settings["interests"],
        int(settings.get("rank_chunk_size", 12)),
    )
    selected = select_articles(
        ranked,
        float(settings["minimum_score"]),
        int(settings["max_selected"]),
        int(settings.get("max_selected_per_source", 2)),
    )

    if not selected:
        markdown = render_empty_markdown(settings.get("timezone", "Asia/Shanghai"), now)
        _write_preview(markdown)
        if dry_run:
            print(markdown)
            return 0
        if settings.get("send_empty_digest", True):
            assert sender is not None
            sender.send_notice(settings["title"], markdown)
        state.mark_success(candidates, set(), now)
        state.save()
        LOG.info("候选均未达到最低分，已记录为处理完成")
        return 0

    if settings.get("fetch_full_text", True):
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(
            timeout=int(settings.get("request_timeout_seconds", 25)),
            follow_redirects=True,
            headers=headers,
        ) as client:
            for item in selected:
                enrich_full_text(item, client, int(settings["full_text_max_characters"]))

    summaries = [
        editor.summarize(item, int(settings["full_text_max_characters"])) for item in selected
    ]
    markdown = render_markdown(
        summaries,
        len(candidates),
        settings.get("timezone", "Asia/Shanghai"),
        now,
    )

    _write_preview(markdown)

    if dry_run:
        print(markdown)
        LOG.info("dry-run：未推送且未更新状态")
        return 0

    assert sender is not None
    sender.send_digest(settings["title"], split_for_feishu(markdown))
    state.mark_success(candidates, {item.id for item in selected}, now)
    state.save()
    LOG.info("成功推送 %d 条内容并更新状态", len(selected))
    return 0


def _notify_failure(exc: Exception) -> None:
    """Best-effort alert that deliberately excludes tracebacks and secret values."""

    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook or os.getenv("DIGEST_DRY_RUN", "").lower() in {"1", "true", "yes"}:
        return
    try:
        sender = FeishuSender(webhook, os.getenv("FEISHU_WEBHOOK_SECRET", "").strip())
        sender.send_notice(
            "AI 前沿技术简报运行失败",
            f"错误类型：`{type(exc).__name__}`\n\n请到 GitHub Actions 查看本次运行日志。",
            template="red",
        )
    except Exception:
        LOG.warning("失败告警也未能发送", exc_info=True)


def _write_preview(markdown: str) -> None:
    output_path = ROOT / "output" / "latest_digest.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    LOG.info("简报预览已写入 %s", output_path)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = parse_args()
    if args.collect_only or args.dry_run:
        os.environ["DIGEST_DRY_RUN"] = "true"
    try:
        return run(args)
    except Exception as exc:
        LOG.exception("任务失败")
        _notify_failure(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
