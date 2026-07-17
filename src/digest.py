from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import ArticleSummary


SEPARATOR = "\n\n---\n\n"


def render_markdown(
    summaries: list[ArticleSummary],
    candidate_count: int,
    timezone_name: str,
    generated_at: datetime | None = None,
    digest_identifier: str = "",
) -> str:
    local_now = generated_at or datetime.now(ZoneInfo(timezone_name))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        local_now = local_now.astimezone(ZoneInfo(timezone_name))

    edition = _edition_name(local_now)
    identifier_line = f"简报 ID：`{digest_identifier}`" if digest_identifier else ""
    sections = [
        "\n".join(
            [line for line in [
                f"**{local_now:%Y-%m-%d} · {edition}**",
                f"本期共评估 **{candidate_count}** 条更新，精选 **{len(summaries)}** 条。",
                identifier_line,
                "所有结论均基于原文或摘要生成，请点击原文核验关键数据。",
            ] if line]
        )
    ]
    for index, summary in enumerate(summaries, 1):
        item = summary.article
        stars = "⭐" * max(1, min(5, round(item.score / 2)))
        lines = [
            f"**{index}. {summary.chinese_title}**",
            f"原题：{item.title}",
            f"分类：{item.ai_category}　推荐：{stars}（{item.score:.1f}/10）",
            f"来源：{item.source}　发布时间：{item.published_at:%Y-%m-%d}",
            f"入选理由：{item.ranking_reason}",
            "",
            f"**一句话结论**：{summary.one_liner}",
            f"**解决的问题**：{summary.problem}",
            "",
            "**核心技术**",
            *[f"- {value}" for value in summary.approach],
            "",
            "**关键结果**",
            *[f"- {value}" for value in summary.findings],
            "",
            "**局限与风险**",
            *[f"- {value}" for value in summary.limitations],
            "",
            f"**适合阅读**：{summary.audience}",
            f"[查看原文]({item.url})",
        ]
        if item.resource_urls:
            lines.extend(
                ["", "**代码与资源**", *[f"- [资源 {number}]({url})" for number, url in enumerate(item.resource_urls, 1)]]
            )
        sections.append("\n".join(lines))
    return SEPARATOR.join(sections).strip() + "\n"


def render_empty_markdown(
    timezone_name: str,
    generated_at: datetime | None = None,
    digest_identifier: str = "",
) -> str:
    local_now = generated_at or datetime.now(ZoneInfo(timezone_name))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(timezone_name))
    local_now = local_now.astimezone(ZoneInfo(timezone_name))
    identifier_line = f"\n\n简报 ID：`{digest_identifier}`" if digest_identifier else ""
    return (
        f"**{local_now:%Y-%m-%d} · {_edition_name(local_now)}**"
        f"{identifier_line}\n\n"
        "本次采集没有发现达到质量阈值且未推送的新内容。任务运行正常，下次继续更新。\n"
    )


def split_for_feishu(markdown: str, max_chars: int = 6000) -> list[str]:
    """Split on article boundaries, then paragraphs, while enforcing the hard limit."""

    if max_chars < 100:
        raise ValueError("max_chars 不能小于 100")
    sections = [section.strip() for section in markdown.split(SEPARATOR) if section.strip()]
    pieces: list[str] = []
    for section in sections:
        pieces.extend(_split_oversized_section(section, max_chars))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{SEPARATOR}{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_oversized_section(section: str, max_chars: int) -> list[str]:
    if len(section) <= max_chars:
        return [section]
    result: list[str] = []
    current = ""
    for paragraph in section.split("\n\n"):
        for piece in _hard_split(paragraph, max_chars):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    result.append(current)
                current = piece
    if current:
        result.append(current)
    return result


def _hard_split(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        split_at = max(window.rfind("\n"), window.rfind("。"), window.rfind("；"))
        if split_at < max_chars // 2:
            split_at = max_chars
        else:
            split_at += 1
        pieces.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        pieces.append(remaining)
    return pieces or [""]


def _edition_name(local_now: datetime) -> str:
    if local_now.weekday() == 0:
        return "周一前沿速递"
    if local_now.weekday() == 4:
        return "周五技术精选"
    return "AI 前沿精选"
