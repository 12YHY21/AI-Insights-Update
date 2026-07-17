from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .ai import DeepSeekEditor, deduplicate_similar_titles, select_articles
from .collectors import CollectionResult, collect_feeds, enrich_articles
from .config import ROOT, RuntimeConfig, load_config
from .digest import (
    render_empty_markdown,
    render_empty_monthly_markdown,
    render_markdown,
    render_monthly_markdown,
    split_for_feishu,
)
from .feishu import FeishuSender
from .models import Article, MonthlyReviewDigest
from .state import StateStore, digest_id


LOG = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 前沿技术简报")
    parser.add_argument("--collect-only", action="store_true", help="只测试采集，不调用 AI、不推送")
    parser.add_argument("--dry-run", action="store_true", help="调用 AI 并生成预览，但不推送、不更新状态")
    parser.add_argument("--monthly", action="store_true", help="生成月度复盘而不是常规周报")
    parser.add_argument("--current-month", action="store_true", help="月报预览使用本月至今，而不是上个自然月")
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
        requested_kind = "monthly" if args.monthly else "weekly"
        if snapshot.get("delivery_kind", "weekly") == requested_kind:
            return 0
        if not config.deepseek_api_key:
            raise RuntimeError("续投完成，但继续生成另一类简报需要 DEEPSEEK_API_KEY")
        LOG.info("续投类型与本次计划不同，继续生成 %s 简报", requested_kind)

    if args.monthly and not args.collect_only:
        return _run_monthly(
            args,
            config,
            state,
            sender,
            now,
            timezone_name,
            edition_date,
            dry_run,
        )

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
        enrich_articles(
            selected,
            int(settings.get("request_timeout_seconds", 25)),
            int(settings["full_text_max_characters"]),
        )

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


def _run_monthly(
    args: argparse.Namespace,
    config: RuntimeConfig,
    state: StateStore,
    sender: FeishuSender | None,
    now: datetime,
    timezone_name: str,
    edition_date: str,
    dry_run: bool,
) -> int:
    settings = config.settings
    monthly = settings.get("monthly_review", {})
    period_start, period_end, period_label = _monthly_period(
        now,
        timezone_name,
        bool(args.current_month),
    )
    processed_rows = state.processed_items_between(period_start, period_end)
    all_history_rows = [row for row in processed_rows if row.get("selected")]
    history_rows = all_history_rows[: int(monthly.get("max_history_items", 32))]
    history = _history_articles(history_rows, settings["feeds"])
    history_ids = {item.id for item in history}
    all_history_ids = {str(row["id"]) for row in all_history_rows}
    previously_scanned = _history_articles(
        [row for row in processed_rows if not row.get("selected")],
        settings["feeds"],
    )

    LOG.info("月度复盘区间 %s：找到 %d 条此前推送", period_label, len(history))
    collection = collect_feeds(
        settings["feeds"],
        period_start,
        int(settings.get("request_timeout_seconds", 25)),
        int(monthly.get("max_candidates", 140)),
        int(monthly.get("max_entries_per_feed", 50)),
    )
    if collection.successful_sources == 0:
        raise RuntimeError("月报的所有信息源均采集失败")

    scan_categories = {str(value) for value in monthly.get("scan_categories", [])}
    scanned_by_id: dict[str, Article] = {}
    for item in [*collection.articles, *previously_scanned]:
        if item.published_at >= period_end or item.id in all_history_ids:
            continue
        if scan_categories and item.source_category not in scan_categories:
            continue
        scanned_by_id.setdefault(item.id, item)
    scanned_news = list(scanned_by_id.values())
    unique_news, local_duplicates = deduplicate_similar_titles(
        scanned_news,
        float(settings.get("title_similarity_threshold", 0.94)),
    )
    unique_news.sort(key=_monthly_candidate_priority, reverse=True)
    news_candidates = unique_news[: int(monthly.get("max_news_candidates", 48))]
    report = _base_report(args, dry_run, now, period_start, collection, news_candidates)
    report.update(
        {
            "monthly_period": period_label,
            "period_end": period_end.isoformat(),
            "history_revisited_count": len(history),
            "previously_evaluated_count": len(previously_scanned),
            "monthly_news_scanned_count": len(scanned_news),
        }
    )
    _write_report(report)

    if not history and not news_candidates:
        identifier = digest_id([], f"monthly:{period_label}")
        markdown = render_empty_monthly_markdown(
            period_label,
            timezone_name,
            now,
            identifier,
        )
        report.update({"digest_id": identifier, "selected_count": 0, "ai_usage": _empty_usage()})
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
                str(settings.get("monthly_title", "AI 前沿月度复盘")),
                markdown,
                [],
                set(),
                now,
                edition_date,
                delivery_kind="monthly",
                updates_last_success=False,
            )
            _archive_delivery(snapshot, report)
        else:
            state.mark_monthly_success(now)
            state.save()
        return 0

    editor = DeepSeekEditor(
        config.deepseek_api_key,
        config.deepseek_base_url,
        config.rank_model,
        config.summary_model,
    )
    selected_news: list[Article] = []
    if news_candidates:
        first_ranked = editor.rank(
            news_candidates,
            settings["interests"],
            int(settings.get("rank_chunk_size", 12)),
        )
        final_ranked = editor.rerank(
            first_ranked,
            settings["interests"],
            min(30, int(monthly.get("max_news_candidates", 48))),
            8,
        )
        selected_news = select_articles(
            final_ranked,
            float(monthly.get("minimum_news_score", 7.0)),
            int(monthly.get("max_news_items", 10)),
            2,
        )

    review_pool = list(history)
    review_pool.extend(item for item in selected_news if item.id not in history_ids)
    if not review_pool:
        identifier = digest_id([item.id for item in news_candidates], f"monthly:{period_label}")
        markdown = render_empty_monthly_markdown(
            period_label,
            timezone_name,
            now,
            identifier,
        )
        report.update(
            _ranking_report(
                news_candidates,
                local_duplicates,
                [],
                editor.usage_report(),
                identifier,
            )
        )
        report["monthly_news_selected_count"] = 0
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
                str(settings.get("monthly_title", "AI 前沿月度复盘")),
                markdown,
                [],
                set(),
                now,
                edition_date,
                delivery_kind="monthly",
                updates_last_success=False,
            )
            _archive_delivery(snapshot, report)
        else:
            state.mark_monthly_success(now)
            state.save()
        return 0

    if settings.get("fetch_full_text", True):
        enrich_articles(
            review_pool,
            int(settings.get("request_timeout_seconds", 25)),
            int(monthly.get("full_text_max_characters", 7000)),
        )
    review = editor.monthly_review(
        review_pool,
        history_ids,
        period_label,
        settings["interests"],
        min(1800, int(monthly.get("full_text_max_characters", 7000))),
    )
    identifier = digest_id([item.id for item in review_pool], f"monthly:{period_label}")
    markdown = render_monthly_markdown(
        review,
        len(scanned_news),
        timezone_name,
        now,
        identifier,
    )
    report.update(
        _monthly_report(
            history,
            news_candidates,
            local_duplicates,
            selected_news,
            review,
            editor.usage_report(),
            identifier,
        )
    )
    _write_preview(markdown)
    _write_report(report)

    if dry_run:
        print(markdown)
        LOG.info("monthly dry-run：未推送且未更新状态")
        return 0

    assert sender is not None
    snapshot = _start_and_deliver(
        sender,
        state,
        identifier,
        str(settings.get("monthly_title", "AI 前沿月度复盘")),
        markdown,
        selected_news,
        _monthly_visible_ids(review),
        now,
        edition_date,
        delivery_kind="monthly",
        updates_last_success=False,
    )
    _archive_delivery(snapshot, report)
    LOG.info("月度复盘推送成功：历史 %d 条，新动态 %d 条", len(history), len(selected_news))
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
    delivery_kind: str = "weekly",
    updates_last_success: bool = True,
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
        delivery_kind=delivery_kind,
        updates_last_success=updates_last_success,
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
    if args.collect_only:
        mode = "collect-only"
    elif args.monthly:
        mode = "monthly-dry-run" if dry_run else "monthly-send"
    else:
        mode = "dry-run" if dry_run else "send"
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


def _monthly_report(
    history: list[Article],
    news_candidates: list[Article],
    local_duplicates: list[Article],
    selected_news: list[Article],
    review: MonthlyReviewDigest,
    usage: dict[str, Any],
    identifier: str,
) -> dict[str, Any]:
    history_ids = {item.id for item in history}
    duplicate_ids = {item.id for item in local_duplicates}
    selected_news_ids = {item.id for item in selected_news}
    top_ids = set(review.top_ids)
    visible_ids = _monthly_visible_ids(review)
    return {
        "digest_id": identifier,
        "selected_count": len(review.top_ids),
        "history_revisited_count": len(history),
        "monthly_news_candidate_count": len(news_candidates),
        "monthly_news_selected_count": len(selected_news),
        "ai_usage": usage,
        "monthly_local_duplicates": [
            {
                "id": item.id,
                "title": item.title,
                "url": item.url,
                "duplicate_of": item.duplicate_of,
            }
            for item in local_duplicates
        ],
        "monthly_reviews": [
            {
                "id": item.article.id,
                "title": item.article.title,
                "url": item.article.url,
                "source": item.article.source,
                "previously_sent": item.article.id in history_ids,
                "importance_score": item.importance_score,
                "verdict": item.verdict,
                "reassessment": item.reassessment,
                "latest_context": item.latest_context,
                "recommendation": item.recommendation,
                "top_item": item.article.id in top_ids,
                "shown_in_digest": item.article.id in visible_ids or item.was_previously_sent,
            }
            for item in review.reviews
        ],
        "monthly_news_ranking": [
            {
                "id": item.id,
                "title": item.title,
                "source": item.source,
                "score": item.score,
                "reason": item.ranking_reason,
                "local_duplicate": item.id in duplicate_ids,
                "selected_for_review": item.id in selected_news_ids,
            }
            for item in news_candidates
        ],
    }


def _monthly_visible_ids(review: MonthlyReviewDigest) -> set[str]:
    return (
        set(review.top_ids)
        | set(review.major_news_ids)
        | {
            item.article.id
            for item in review.reviews
            if item.verdict in {"影响有限", "待验证"}
        }
    )


def _monthly_period(
    now: datetime,
    timezone_name: str,
    current_month: bool,
) -> tuple[datetime, datetime, str]:
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    this_month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if current_month:
        start = this_month_start
        end = local_now
        label = f"{start:%Y年%m月}（截至{local_now:%m月%d日}）"
    else:
        end = this_month_start
        previous_day = this_month_start - timedelta(days=1)
        start = previous_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"{start:%Y年%m月}"
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label


def _history_articles(
    rows: list[dict[str, Any]],
    feeds: list[dict[str, Any]],
) -> list[Article]:
    categories = {str(feed["name"]): str(feed.get("category", "其他")) for feed in feeds}
    articles: list[Article] = []
    for row in rows:
        timestamp = row.get("published_at") or row.get("processed_at")
        try:
            published_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except (TypeError, ValueError):
            published_at = datetime.now(timezone.utc)
        source = str(row.get("source", "历史推送"))
        articles.append(
            Article(
                id=str(row["id"]),
                title=str(row.get("title", "未命名内容")),
                url=str(row.get("url", "")),
                source=source,
                source_category=str(row.get("source_category") or categories.get(source, "其他")),
                published_at=published_at,
                summary=str(
                    row.get("summary")
                    or "此前已经过系统评估；月度复盘将重新读取原文并与本月新闻对照。"
                ),
            )
        )
    return articles


def _monthly_candidate_priority(article: Article) -> tuple[int, int, datetime]:
    title = article.title.casefold()
    major_terms = (
        "gpt",
        "gemini",
        "claude",
        "deepseek",
        "qwen",
        "llama",
        "release",
        "introducing",
        "launch",
        "发布",
        "模型",
    )
    return (
        int(any(term in title for term in major_terms)),
        int(article.source_category == "官方动态"),
        article.published_at,
    )


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
    delivery_kind = str(snapshot.get("delivery_kind", "weekly"))
    if delivery_kind == "monthly":
        digest_path = ROOT / "digests" / "monthly" / year / f"{edition_date}-{identifier}.md"
        report_path = ROOT / "reports" / "monthly" / year / f"{edition_date}-{identifier}.json"
    else:
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
        "delivery_kind": snapshot.get("delivery_kind", "weekly"),
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
