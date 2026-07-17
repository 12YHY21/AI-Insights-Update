from __future__ import annotations

import json
import logging
from collections.abc import Iterable

from openai import OpenAI

from .models import Article, ArticleSummary


LOG = logging.getLogger(__name__)


class DeepSeekEditor:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        rank_model: str,
        summary_model: str,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=2, timeout=120)
        self.rank_model = rank_model
        self.summary_model = summary_model

    def _json_completion(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                content = response.choices[0].message.content or ""
                if not content.strip():
                    raise RuntimeError("DeepSeek 返回了空内容")
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError("DeepSeek JSON 顶层不是对象")
                return parsed
            except Exception as exc:
                last_error = exc
                LOG.warning("DeepSeek JSON 请求第 %d/3 次失败：%s", attempt, exc)
        raise RuntimeError("DeepSeek 连续三次未返回有效 JSON") from last_error

    def rank(
        self,
        articles: list[Article],
        interests: list[str],
        chunk_size: int = 12,
    ) -> list[Article]:
        by_id = {item.id: item for item in articles}
        for chunk in _chunks(articles, chunk_size):
            payload = [
                {
                    "id": item.id,
                    "title": item.title,
                    "source": item.source,
                    "source_category": item.source_category,
                    "published_at": item.published_at.isoformat(),
                    "abstract": item.summary[:3500],
                }
                for item in chunk
            ]
            result = self._json_completion(
                self.rank_model,
                """你是严谨的 AI 技术情报编辑。只能根据输入的标题、摘要和来源进行判断。
请输出一个 json 对象，格式为：
{"items":[{"id":"输入中的原始 id","score":0到10,"category":"技术分类","reason":"不超过60字的入选或降分理由"}]}
必须返回每个输入项目且 id 原样保留。评分权重：前沿性30%、技术创新25%、实际价值20%、来源可信度15%、用户兴趣相关性10%。
纯产品宣传、重复新闻、没有技术细节或摘要不足的内容应低分。不要根据常识补充输入中没有的事实。""",
                json.dumps({"interests": interests, "articles": payload}, ensure_ascii=False),
                max_tokens=5000,
            )
            rows = result.get("items", [])
            if not isinstance(rows, list):
                raise RuntimeError("DeepSeek 排序结果缺少 items 列表")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = by_id.get(str(row.get("id", "")))
                if not item:
                    continue
                try:
                    item.score = max(0.0, min(10.0, float(row.get("score", 0))))
                except (TypeError, ValueError):
                    item.score = 0.0
                item.ai_category = _text(row.get("category"), "其他", 40)
                item.ranking_reason = _text(row.get("reason"), "未说明", 200)
        return sorted(articles, key=lambda item: (item.score, item.published_at), reverse=True)

    def summarize(self, article: Article, max_characters: int) -> ArticleSummary:
        source_text = (article.content or article.summary)[:max_characters]
        result = self._json_completion(
            self.summary_model,
            """你是严谨的中文 AI 技术编辑。只能根据用户提供的 source_text 总结，不得用记忆补全事实或数据。
资料未给出时必须明确写“原文未说明”。保留关键方法名、模型名和量化结果，语言适合技术人员手机阅读。
输出一个 json 对象，格式为：
{"chinese_title":"准确、简洁的中文标题","one_liner":"一句话结论","problem":"解决的问题","approach":["2至4项技术路线"],"findings":["1至4项结果"],"limitations":["1至3项局限"],"audience":"适合谁阅读"}
不要输出 markdown，不要在 JSON 外输出其他文字。""",
            json.dumps(
                {
                    "original_title": article.title,
                    "source": article.source,
                    "url": article.url,
                    "source_text": source_text,
                },
                ensure_ascii=False,
            ),
            max_tokens=3000,
        )
        return ArticleSummary(
            article=article,
            chinese_title=_text(result.get("chinese_title"), article.title, 180),
            one_liner=_text(result.get("one_liner"), "原文未说明", 800),
            problem=_text(result.get("problem"), "原文未说明", 800),
            approach=_string_list(result.get("approach")),
            findings=_string_list(result.get("findings")),
            limitations=_string_list(result.get("limitations")),
            audience=_text(result.get("audience"), "AI 技术从业者", 300),
        )


def select_articles(
    ranked: list[Article],
    minimum_score: float,
    max_selected: int,
    max_per_source: int,
) -> list[Article]:
    """Select high-scoring items while preventing one source from filling the digest."""

    selected: list[Article] = []
    counts: dict[str, int] = {}
    for article in ranked:
        if article.score < minimum_score:
            continue
        if counts.get(article.source, 0) >= max_per_source:
            continue
        selected.append(article)
        counts[article.source] = counts.get(article.source, 0) + 1
        if len(selected) >= max_selected:
            break
    return selected


def _chunks(items: list[Article], size: int) -> Iterable[list[Article]]:
    if size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _text(value: object, fallback: str, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit] or fallback


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return ["原文未说明"]
    cleaned = [str(item).strip()[:500] for item in value if str(item).strip()]
    return cleaned[:4] or ["原文未说明"]

