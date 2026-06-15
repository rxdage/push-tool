"""Claude 摘要 + 分类（严格 JSON）。把对应 InterestProfile 放进 system prompt 做个性化。

逐条调用可并发（summarize_items）；日/周批量跑走 Message Batches API（build_batch_requests +
submit_batch + collect_batch）省钱。

版权红线：只存摘要片段；输出一律改写（paraphrase），单条引用不超过 15 词，绝不复制原文。
"""
from __future__ import annotations

import asyncio
import json

from anthropic import AsyncAnthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from app.config import settings
from app.models import InterestProfile, Item

# 逐条摘要/分类默认 haiku 省钱；要质量可换 sonnet（见 settings 或调用方传参）。
DEFAULT_MODEL = "claude-haiku-4-5"

# 结构化输出 schema：单条候选的判定结果。
# 注：json_schema 不支持 null / 数值约束，故 action_label 用 "none" 兜底，relevance 在代码侧夹取。
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "number"},
        "classification": {"type": "string", "enum": ["new", "classic"]},
        "action_label": {"type": "string", "enum": ["action", "watch", "none"]},
        "summary": {"type": "string"},
        "why_it_matters": {"type": "string"},
    },
    "required": [
        "relevance",
        "classification",
        "action_label",
        "summary",
        "why_it_matters",
    ],
    "additionalProperties": False,
}

OUTPUT_CONFIG = {"format": {"type": "json_schema", "schema": RESULT_SCHEMA}}


def build_system_prompt(profile: InterestProfile, feed_type: str) -> str:
    """第 6 节模板。industry 源额外要 action/watch + why_it_matters。"""
    industry = feed_type == "industry"
    lines = [
        "你是为某用户做个性化情报/文献筛选的分析师。",
        f"兴趣画像：{profile.description}",
        f"关键词：{', '.join(profile.keywords)}",
        f"优先：{', '.join(profile.must_have)}",
        f"排除：{', '.join(profile.exclude)}",
        "",
        "对该候选条目：",
        "1) 给相关性 relevance 0–1（越相关越高）；",
        "2) classification 取 [new|classic]；",
    ]
    if industry:
        lines.append("3) action_label 取 [action|watch]（无明确行动价值用 none）；")
        lines.append("4) 写一条 ≤40 字的改写式 summary（绝不照抄原文，单引用 <15 词）；")
        lines.append(
            "5) 若 action_label=action，再写 ≤25 字 why_it_matters，"
            '明确"这对超薄 SiN 膜平台意味着什么"；否则 why_it_matters 留空字符串。'
        )
    else:
        lines.append('3) action_label 一律 "none"；')
        lines.append("4) 写一条 ≤40 字的改写式 summary（绝不照抄原文，单引用 <15 词）；")
        lines.append('5) why_it_matters 留空字符串。')
    lines += [
        "",
        "全部改写，禁止复制来源文字。严格按结构化输出返回。",
    ]
    return "\n".join(lines)


def build_user_content(item: Item) -> str:
    abstract = (item.abstract or "")[:2000]
    return f"标题：{item.title}\n摘要：{abstract}"


def _normalize(raw: dict) -> dict:
    """夹取 relevance；'none'/'' → None。"""
    rel = raw.get("relevance", 0.0)
    try:
        rel = max(0.0, min(1.0, float(rel)))
    except (TypeError, ValueError):
        rel = 0.0
    action = raw.get("action_label")
    if action == "none":
        action = None
    why = (raw.get("why_it_matters") or "").strip() or None
    return {
        "relevance": rel,
        "classification": raw.get("classification"),
        "action_label": action,
        "summary": (raw.get("summary") or "").strip(),
        "why_it_matters": why,
    }


# ---- 逐条并发 ----

async def summarize_item(
    client: AsyncAnthropic,
    item: Item,
    system_prompt: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    resp = await client.messages.create(
        model=model,
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": build_user_content(item)}],
        output_config=OUTPUT_CONFIG,
    )
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return _normalize(json.loads(text))


async def summarize_items(
    items: list[Item],
    profile: InterestProfile,
    feed_type: str,
    model: str = DEFAULT_MODEL,
    concurrency: int = 5,
) -> dict[int, dict]:
    """并发逐条；返回 {item_id: result}。单条失败记 None 跳过。"""
    if not items:
        return {}
    system_prompt = build_system_prompt(profile, feed_type)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    sem = asyncio.Semaphore(concurrency)

    async def one(it: Item):
        async with sem:
            try:
                return it.id, await summarize_item(client, it, system_prompt, model)
            except Exception as e:  # noqa: BLE001
                print(f"  ! summarize item#{it.id} 失败: {e}")
                return it.id, None

    pairs = await asyncio.gather(*(one(it) for it in items))
    return {iid: res for iid, res in pairs if res is not None}


# ---- 批量（Message Batches API，50% 价）----

def build_batch_requests(
    items: list[Item],
    profile: InterestProfile,
    feed_type: str,
    model: str = DEFAULT_MODEL,
) -> list[Request]:
    """构造批请求；custom_id = f'item-{id}'，共享 system 走 prompt cache。"""
    system_prompt = build_system_prompt(profile, feed_type)
    shared_system = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    return [
        Request(
            custom_id=f"item-{it.id}",
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=512,
                system=shared_system,
                messages=[{"role": "user", "content": build_user_content(it)}],
                output_config=OUTPUT_CONFIG,
            ),
        )
        for it in items
    ]


def collect_batch_results(client, batch_id: str) -> dict[int, dict]:
    """拉取批结果，返回 {item_id: result}。成功才计入。"""
    out: dict[int, dict] = {}
    for r in client.messages.batches.results(batch_id):
        if r.result.type != "succeeded":
            continue
        item_id = int(r.custom_id.removeprefix("item-"))
        msg = r.result.message
        text = next((b.text for b in msg.content if b.type == "text"), "{}")
        out[item_id] = _normalize(json.loads(text))
    return out
