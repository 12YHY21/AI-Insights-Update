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
) -> str:
    local_now = generated_at or datetime.now(ZoneInfo(timezone_name))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        local_now = local_now.astimezone(ZoneInfo(timezone_name))

    sections = [
        "\n".join(
            [
                f"**{local_now:%Y-%m-%d} · AI 前沿精选**",
                f"本期共评估 **{candidate_count}** 条更新，精选 **{len(summaries)}** 条。",
                "所有结论均基于原文或摘要生成，请点击原文核验关键数据。",
            ]
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
        sections.append("\n".join(lines))
    return SEPARATOR.join(sections).strip() + "\n"


def render_empty_markdown(timezone_name: str, generated_at: datetime | None = None) -> str:
    local_now = generated_at or datetime.now(ZoneInfo(timezone_name))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(timezone_name))
    return (
        f"**{local_now.astimezone(ZoneInfo(timezone_name)):%Y-%m-%d} · AI 前沿精选**\n\n"
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
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)] or [""]

