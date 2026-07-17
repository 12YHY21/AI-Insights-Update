from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .ai import DeepSeekEditor, deduplicate_similar_titles, select_articles
from .collectors import USER_AGENT, CollectionResult, collect_feeds, enrich_full_text
from .config import ROOT, load_config
from .digest import render_empty_markdown, render_markdown, split_for_feishu
from .feishu import FeishuSender
from .models import Article
from .state import StateStore, digest_id


LOG = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 前沿技术简报")
    parser.add_argument("--collect-only", action="store_true", help="只测试采集，不调用 AI、不推送")
    parser.add_argument("--dry-run", action="store_true", help="调用 AI 并生成预览，但不推送、不更新状态")
    parser.add_argument("--state-path", default=str(ROOT / "data" / "state.json"))
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    dry_run = args.dry_run or os.getenv("DIGEST_DRY_RUN", "").lower() in {"1", "true", "yes"}
    actual_send = not (args.collect_only or dry_run)
    state = StateStore(Path(args.state_path))
    config = load_config(
        require_ai=not args.collect_only and not (actual_send and state.pending_delivery),
        require_feishu=actual_send,
    )
    settings = config.settings
    now = datetime.now(timezone.utc)
    timezone_name = settings.get("timezone", "Asia/Shanghai")
    edition_date = now.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    sender = _sender(config, settings) if actual_send else None

    if actual_send and state.pending_delivery:
        assert sender is not None
        pending = state.pending_delivery
        LOG.warning("检测到未完成简报 %s，将从未发送分片继续", pending.get("id"))
        _write_preview(str(pending.get("markdown", "")))
        snapshot = _deliver_pending(sender, state, now)
        _archive_delivery(snapshot, _resume_report(snapshot, now))
        LOG.info("未完成简报已恢复并全部投递：%s", snapshot["id"])
        return 0

    cutoff = state.cutoff(
        int(settings["lookback_days_on_first_run"]),
        int(settings.get("overlap_hours", 48)),
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
    candidates = [item for item in collection.articles if not state.has_seen(item.id)]
    report = _base_report(args, dry_run, now, cutoff, collection, candidates)
    _write_report(report)
    if collection.successful_sources == 0:
        raise RuntimeError("所有信息源均采集失败，拒绝推进状态窗口")
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

    if not candidates:
        identifier = digest_id([], edition_date)
        markdown = render_empty_markdown(timezone_name, now, identifier)
        report.update({"digest_id": identifier, "selected_count": 0, "ai_usage": _empty_usage()})
        _write_preview(markdown)
        _write_report(report)
        if dry_run:
            print(markdown)
            return 0
        if settings.get("send_empty_digest", True):
            assert sender is not None
            snapshot = _start_and_deliver(
                sender, state, identifier, settings["title"], markdown, [], set(), now, edition_date
            )
            _archive_delivery(snapshot, report)
        else:
            state.mark_success([], set(), now)
            state.save()
        return 0

    unique_candidates, local_duplicates = deduplicate_similar_titles(
        candidates,
        float(settings.get("title_similarity_threshold", 0.94)),
    )
    LOG.info("标题语义去重：%d 条候选，移除 %d 条近重复", len(unique_candidates), len(local_duplicates))
    editor = DeepSeekEditor(
        config.deepseek_api_key,
        config.deepseek_base_url,
        config.rank_model,
        config.summary_model,
    )
    first_ranked = editor.rank(
        unique_candidates,
        settings["interests"],
        int(settings.get("rank_chunk_size", 12)),
    )
    final_ranked = editor.rerank(
        first_ranked,
        settings["interests"],
        int(settings.get("rerank_top_n", 20)),
        int(settings.get("rerank_per_category", 4)),
    )
    selected = select_articles(
        final_ranked,
        float(settings["minimum_score"]),
        int(settings["max_selected"]),
        int(settings.get("max_selected_per_source", 2)),
        {str(key): int(value) for key, value in settings.get("category_maximums", {}).items()},
        {str(key): int(value) for key, value in settings.get("category_minimums", {}).items()},
    )
    identifier = digest_id([item.id for item in selected] or [item.id for item in candidates], edition_date)

    if not selected:
        markdown = render_empty_markdown(timezone_name, now, identifier)
        report.update(
            _ranking_report(candidates, local_duplicates, selected, editor.usage_report(), identifier)
        )
        _write_preview(markdown)
        _write_report(report)
        if dry_run:
            print(markdown)
            return 0
        if settings.get("send_empty_digest", True):
            assert sender is not None
            snapshot = _start_and_deliver(
                sender,
                state,
                identifier,
                settings["title"],
                markdown,
                candidates,
                set(),
                now,
                edition_date,
            )
            _archive_delivery(snapshot, report)
        else:
            state.mark_success(candidates, set(), now)
            state.save()
        return 0

    if settings.get("fetch_full_text", True):
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
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
        timezone_name,
        now,
        identifier,
    )
    report.update(_ranking_report(candidates, local_duplicates, selected, editor.usage_report(), identifier))
    _write_preview(markdown)
    _write_report(report)

    if dry_run:
        print(markdown)
        LOG.info("dry-run：未推送且未更新状态")
        return 0

    assert sender is not None
    snapshot = _start_and_deliver(
        sender,
        state,
        identifier,
        settings["title"],
        markdown,
        candidates,
        {item.id for item in selected},
        now,
        edition_date,
    )
    _archive_delivery(snapshot, report)
    LOG.info("成功推送 %d 条内容并更新状态，简报 ID：%s", len(selected), identifier)
    return 0


def _start_and_deliver(
    sender: FeishuSender,
    state: StateStore,
    identifier: str,
    title: str,
    markdown: str,
    processed: list[Article],
    selected_ids: set[str],
    now: datetime,
    edition_date: str,
) -> dict[str, Any]:
    chunks = split_for_feishu(markdown)
    state.start_delivery(
        identifier,
        title,
        markdown,
        chunks,
        processed,
        selected_ids,
        now,
        edition_date,
        archive=True,
    )
    state.save()
    return _deliver_pending(sender, state, now)


def _deliver_pending(sender: FeishuSender, state: StateStore, now: datetime) -> dict[str, Any]:
    pending = state.pending_delivery
    if not pending:
        raise RuntimeError("没有可恢复的飞书投递")
    chunks = [str(value) for value in pending.get("chunks", [])]
    sent = {int(value) for value in pending.get("sent_chunk_indexes", [])}
    total = len(chunks)
    for index, chunk in enumerate(chunks):
        if index in sent:
            continue
        sequence = f"（{index + 1}/{total}）" if total > 1 else ""
        sender.send_markdown_card(str(pending["title"]), chunk, sequence)
        state.mark_delivery_chunk_sent(index)
        state.save()
        LOG.info("飞书分片投递成功：%d/%d", index + 1, total)
        if index + 1 < total:
            time.sleep(0.4)
    snapshot = state.complete_delivery(now)
    state.save()
    return snapshot


def _sender(config: Any, settings: dict[str, Any]) -> FeishuSender:
    return FeishuSender(
        config.feishu_webhook_url,
        config.feishu_webhook_secret,
        int(settings.get("request_timeout_seconds", 25)),
    )


def _base_report(
    args: argparse.Namespace,
    dry_run: bool,
    now: datetime,
    cutoff: datetime,
    collection: CollectionResult,
    candidates: list[Article],
) -> dict[str, Any]:
    mode = "collect-only" if args.collect_only else "dry-run" if dry_run else "send"
    return {
        "schema_version": 1,
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "mode": mode,
        "generated_at": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "collected_count": len(collection.articles),
        "candidate_count": len(candidates),
        "successful_sources": collection.successful_sources,
        "failed_sources": collection.failed_sources,
        "sources": [item.to_dict() for item in collection.sources],
    }


def _ranking_report(
    candidates: list[Article],
    local_duplicates: list[Article],
    selected: list[Article],
    usage: dict[str, Any],
    identifier: str,
) -> dict[str, Any]:
    duplicate_ids = {item.id for item in local_duplicates}
    selected_ids = {item.id for item in selected}
    return {
        "digest_id": identifier,
        "selected_count": len(selected),
        "ai_usage": usage,
        "articles": [
            {
                "id": item.id,
                "title": item.title,
                "url": item.url,
                "source": item.source,
                "source_category": item.source_category,
                "score": item.score,
                "reason": item.ranking_reason,
                "duplicate_of": item.duplicate_of,
                "local_duplicate": item.id in duplicate_ids,
                "selected": item.id in selected_ids,
            }
            for item in candidates
        ],
    }


def _empty_usage() -> dict[str, Any]:
    return {"models": {}, "total_requests": 0, "total_tokens": 0}


def _write_preview(markdown: str) -> None:
    output_path = ROOT / "output" / "latest_digest.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    LOG.info("简报预览已写入 %s", output_path)


def _write_report(report: dict[str, Any]) -> None:
    output_path = ROOT / "output" / "run_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _archive_delivery(snapshot: dict[str, Any], report: dict[str, Any]) -> None:
    if not snapshot.get("archive", True):
        return
    edition_date = str(snapshot["edition_date"])
    year, month, _ = edition_date.split("-")
    identifier = str(snapshot["id"])
    digest_path = ROOT / "digests" / year / month / f"{edition_date}-{identifier}.md"
    report_path = ROOT / "reports" / year / month / f"{edition_date}-{identifier}.json"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(str(snapshot.get("markdown", "")), encoding="utf-8")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    LOG.info("简报和运行报告已归档：%s", digest_path)


def _resume_report(snapshot: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "mode": "resume-send",
        "generated_at": now.isoformat(),
        "digest_id": snapshot.get("id"),
        "selected_count": len(snapshot.get("selected_ids", [])),
        "resumed_delivery": True,
        "sent_chunk_count": len(snapshot.get("chunks", [])),
        "ai_usage": _empty_usage(),
    }


def _notify_failure(exc: Exception) -> None:
    """Best-effort alert that deliberately excludes tracebacks and secret values."""

    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook or os.getenv("DIGEST_DRY_RUN", "").lower() in {"1", "true", "yes"}:
        return
    run_url = ""
    if os.getenv("GITHUB_RUN_ID") and os.getenv("GITHUB_REPOSITORY"):
        run_url = (
            f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
            f"{os.environ['GITHUB_RUN_ID']}"
        )
    content = f"错误类型：`{type(exc).__name__}`\n\n请查看 GitHub Actions 运行日志。"
    if run_url:
        content += f"\n\n[打开本次运行]({run_url})"
    try:
        sender = FeishuSender(webhook, os.getenv("FEISHU_WEBHOOK_SECRET", "").strip())
        sender.send_notice("AI 前沿技术简报运行失败", content, template="red")
    except Exception:
        LOG.warning("失败告警也未能发送", exc_info=True)


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
