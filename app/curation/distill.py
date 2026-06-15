"""把学术语料蒸馏成"领域知识 Skill"（SKILL.md）。

一次性/定期跑：复用库里已收集的学术文献（改写摘要/摘要），让 Claude 归纳成结构化领域简报
+ 文献索引，写到 skills/<name>/SKILL.md。Claude(Code/API) 加载后可当该领域助手成体系答问。

版权：输入是已收集的摘要，输出是二次归纳 + 链接，不复制原文。

用法：
    python -m app.curation.distill                       # 默认固态纳米孔，取近 400 条
    python -m app.curation.distill --limit 300 --feed academic --name nanopore-radar
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Digest, DigestItem, Item, Subscription

# 蒸馏用质量更好的模型（一次性，值得）
DISTILL_MODEL = "claude-sonnet-4-6"

SYSTEM = """你是该领域的资深分析师。下面给你一批本领域近期文献（标题 + 摘要 + 年份 + 链接编号）。
请把它们蒸馏成一份**结构化领域知识简报**，供专业读者快速建立体系认知。用中文输出 Markdown，包含：

## 领域概览
（2-3 句这个领域当前在做什么、核心目标）

## 主流技术路线
（按"制造工艺 / 传感应用 / 材料体系 / 数据与算法"等维度归纳，每条点明代表做法与权衡）

## 近期趋势与热点
（从这批文献里提炼 3-6 个正在发生的方向）

## 经典基石
（奠基性/高影响工作，若文献里有标记为 classic 的优先）

## 待解难题
（公认的瓶颈/开放问题）

## 代表团队与机构
（若摘要中可辨识，列出活跃团队/公司；辨识不出就略）

要求：全部改写归纳，不照抄原文句子；涉及具体结论处用 [n] 标注对应文献编号（编号见输入）。
精炼、可信、不堆砌。"""


async def _load_corpus(
    session: AsyncSession, feed_type: str, recent: int
) -> list[tuple[str, Item]]:
    """取**已进过 digest 的策展条目**（过了相关性闸门、带改写摘要），按 item 去重。

    新为主、经典为补充、且 token 可控：取最近 `recent` 条"新"条目 + **全部**经典条目。
    库再大，蒸馏输入也只随 recent 线性增长，不会爆 token。

    用策展集而非原始 items —— 期刊源（Nature Nano/ACS Nano…）原始内容主题极杂，会污染蒸馏。
    """
    sub_ids = (
        await session.execute(
            select(Subscription.id).where(Subscription.feed_type == feed_type)
        )
    ).scalars().all()
    if not sub_ids:
        return []
    rows = await session.execute(
        select(DigestItem.summary, DigestItem.classification, Item)
        .join(Item, DigestItem.item_id == Item.id)
        .join(Digest, DigestItem.digest_id == Digest.id)
        .where(Digest.subscription_id.in_(sub_ids))
        .order_by(Item.published_at.desc().nullslast(), Item.fetched_at.desc())
    )
    seen: set[int] = set()
    news: list[tuple[str, Item]] = []
    classics: list[tuple[str, Item]] = []
    for summary, classification, it in rows.all():
        if it.id in seen:
            continue
        seen.add(it.id)
        is_classic = classification == "classic" or "classic" in (it.tags or [])
        if is_classic and "classic" not in (it.tags or []):
            it.tags = (it.tags or []) + ["classic"]
        (classics if is_classic else news).append((summary or it.abstract or "", it))
    return news[:recent] + classics  # 新为主（最近 recent）+ 经典全收（补充）


def _corpus_text(pairs: list[tuple[str, Item]]) -> str:
    lines = []
    for i, (summary, it) in enumerate(pairs, 1):
        year = it.published_at.year if it.published_at else "?"
        extra = (it.abstract or "")[:400].replace("\n", " ")
        flag = " [classic]" if "classic" in (it.tags or []) else ""
        lines.append(f"[{i}]{flag} ({year}) {it.title}\n    摘要：{summary}\n    原摘要：{extra}")
    return "\n".join(lines)


def _references_md(pairs: list[tuple[str, Item]]) -> str:
    out = ["# 文献索引（蒸馏来源）\n"]
    for i, (_summary, it) in enumerate(pairs, 1):
        year = it.published_at.year if it.published_at else "?"
        url = it.url or ""
        out.append(f"{i}. ({year}) {it.title} {url}".rstrip())
    return "\n".join(out)


async def distill(feed_type: str, recent: int, name: str, out_dir: Path) -> dict:
    async with SessionLocal() as session:
        items = await _load_corpus(session, feed_type, recent)
    if not items:
        return {"status": "empty", "reason": f"没有 {feed_type} 策展语料（先跑过 digest）"}

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=DISTILL_MODEL,
        max_tokens=8000,
        system=[
            {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": _corpus_text(items)}],
    )
    body = next((b.text for b in resp.content if b.type == "text"), "")

    desc = (
        f"{name}：基于持续收集的文献蒸馏的领域知识。"
        f"当用户询问该领域（制造工艺 / 传感应用 / 经典与趋势）时加载本 skill 作答。"
    )
    skill_md = (
        f"---\nname: {name}\n"
        f"description: {desc}\n---\n\n"
        f"> 本 skill 由 push-tool 从 {len(items)} 条收集文献自动蒸馏（改写归纳 + 链接，不含原文）。\n"
        f"> 文献编号见同目录 references.md。\n\n"
        f"{body}\n"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (out_dir / "references.md").write_text(_references_md(items), encoding="utf-8")
    usage = resp.usage
    return {
        "status": "ok",
        "items": len(items),
        "out": str(out_dir / "SKILL.md"),
        "body": body,  # 蒸馏正文（供综述推送复用，0 额外 token）
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


async def _main(args) -> None:
    out_dir = Path(args.out or f"skills/{args.name}")
    res = await distill(args.feed, args.recent, args.name, out_dir)
    print(res)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--feed", default="academic")
    p.add_argument("--recent", type=int, default=120, help="取最近 N 条新条目 + 全部经典")
    p.add_argument("--name", default="nanopore-radar")
    p.add_argument("--out", default="")
    args = p.parse_args()
    asyncio.run(_main(args))
