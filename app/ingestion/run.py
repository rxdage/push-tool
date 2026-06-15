"""抓取入口：跑某订阅（或全部）的 active sources，入库 Item。

用法：
    python -m app.ingestion.run            # 所有订阅的所有 active 源
    python -m app.ingestion.run 1          # 仅 subscription_id=1
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.ingestion.registry import build_adapter
from app.ingestion.store import StoreResult, store_items
from app.models import Source, Subscription


async def fetch_source(session: AsyncSession, source: Source) -> StoreResult | None:
    try:
        adapter = build_adapter(source.kind, source.config_json, settings=settings)
        raws = await adapter.fetch()
    except Exception as e:  # 单源失败不影响其它源
        print(f"  ! source#{source.id} [{source.kind}] {adapter_name(source)} 失败: {e}")
        return None
    result = await store_items(session, source, raws)
    print(f"  {adapter_name(source)} -> {result}")
    return result


def adapter_name(source: Source) -> str:
    return (source.config_json or {}).get("name") or source.kind


async def run(subscription_id: int | None = None) -> None:
    async with SessionLocal() as session:
        sub_q = select(Subscription).where(Subscription.active.is_(True))
        if subscription_id is not None:
            sub_q = sub_q.where(Subscription.id == subscription_id)
        subs = (await session.execute(sub_q)).scalars().all()
        if not subs:
            print("没有匹配的 active 订阅。")
            return

        for sub in subs:
            print(f"[订阅] {sub.name} (id={sub.id})")
            sources = (
                await session.execute(
                    select(Source).where(
                        Source.subscription_id == sub.id, Source.active.is_(True)
                    )
                )
            ).scalars().all()
            for src in sources:
                await fetch_source(session, src)
            await session.commit()
    print("抓取完成。")


if __name__ == "__main__":
    sid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(run(sid))
