"""经典论文：人工种子清单入库为 Item + S2 高引用兜底，每期轮换 1–2 篇"经典回顾"。

轮换用 digest 序号取模，避免依赖时间随机源（可复现）。
经典种子挂在一个 kind='classic' 的 Source 下（active=False，抓取流程跳过它）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Source, Subscription

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
CLASSIC_KIND = "classic"


def load_classic_seeds(filename: str = "classics_nanopore.yaml") -> list[dict]:
    path = SEED_DIR / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def rotate_classics(seeds: list, n: int, offset: int) -> list:
    """从 seeds 里轮换取 n 篇；offset 一般传已生成 digest 数，保证轮换。"""
    if not seeds or n <= 0:
        return []
    start = offset % len(seeds)
    return [seeds[(start + i) % len(seeds)] for i in range(min(n, len(seeds)))]


def _seed_to_item_fields(seed: dict) -> dict:
    doi = seed.get("doi")
    year = seed.get("year")
    published = datetime(int(year), 1, 1, tzinfo=timezone.utc) if year else None
    note = seed.get("note") or ""
    authors = seed.get("authors") or ""
    abstract = f"{note}（{authors}, {year}）".strip("（）") if note else note
    return {
        "external_id": f"doi:{doi}" if doi else f"classic:{seed.get('title','')[:64]}",
        "url": f"https://doi.org/{doi}" if doi else None,
        "title": seed.get("title", "").strip(),
        "abstract": abstract or None,
        "published_at": published,
        "tags": ["classic"],
        "raw_json": {"authors": authors, "year": year, "note": note},
    }


async def ensure_classic_source(
    session: AsyncSession, subscription: Subscription
) -> Source:
    """取/建该订阅的经典种子 Source（active=False，抓取跳过）。"""
    res = await session.execute(
        select(Source).where(
            Source.subscription_id == subscription.id, Source.kind == CLASSIC_KIND
        )
    )
    src = res.scalar_one_or_none()
    if src is None:
        src = Source(
            subscription_id=subscription.id,
            kind=CLASSIC_KIND,
            config_json={"name": "经典种子"},
            weight=1.0,
            active=False,
        )
        session.add(src)
        await session.flush()
    return src


async def ensure_classic_items(
    session: AsyncSession,
    subscription: Subscription,
    filename: str = "classics_nanopore.yaml",
) -> list[Item]:
    """把种子清单幂等入库为 Item（按 external_id 去重），返回该订阅全部经典 Item。"""
    src = await ensure_classic_source(session, subscription)
    existing = {
        ext
        for (ext,) in (
            await session.execute(
                select(Item.external_id).where(Item.source_id == src.id)
            )
        ).all()
        if ext
    }
    for seed in load_classic_seeds(filename):
        fields = _seed_to_item_fields(seed)
        if fields["external_id"] in existing:
            continue
        session.add(Item(source_id=src.id, **fields))
    await session.flush()

    rows = await session.execute(
        select(Item).where(Item.source_id == src.id).order_by(Item.id)
    )
    return list(rows.scalars().all())


def select_classics(
    classic_items: list[Item],
    fresh_high_citation: list[Item],
    max_classic: int,
    offset: int,
) -> list[Item]:
    """轮换选 max_classic 篇经典；人工种子不足时用 S2 高引用兜底。"""
    if max_classic <= 0:
        return []
    picked = rotate_classics(classic_items, max_classic, offset)
    if len(picked) < max_classic:
        picked_ids = {it.id for it in picked}
        ranked = sorted(
            fresh_high_citation,
            key=lambda it: (it.raw_json or {}).get("citationCount", 0),
            reverse=True,
        )
        for it in ranked:
            if it.id in picked_ids:
                continue
            picked.append(it)
            picked_ids.add(it.id)
            if len(picked) >= max_classic:
                break
    return picked[:max_classic]
