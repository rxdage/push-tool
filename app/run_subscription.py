"""手动端到端跑一个订阅：[抓取] → 筛选 pipeline → 投递。

用法：
    python -m app.run_subscription <subscription_id> [--fetch] [--no-deliver] [--batch]

    --fetch       先跑抓取（默认不抓，假设已 ingest）
    --no-deliver  只生成 digest 不投递
    --batch       summarize 走 Message Batches API（省钱，稍慢）
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app.config import settings
from app.curation.pipeline import PipelineConfig, run_pipeline
from app.db import SessionLocal
from app.delivery.deliver import deliver_digest
from app.ingestion.run import run as run_ingest
from app.models import DigestItem, Subscription


async def main(sub_id: int, do_fetch: bool, deliver: bool, use_batch: bool) -> None:
    if do_fetch:
        print(f"[1/3] 抓取 subscription#{sub_id} ...")
        await run_ingest(sub_id)

    async with SessionLocal() as session:
        sub = await session.get(Subscription, sub_id)
        if sub is None:
            print(f"未找到 subscription#{sub_id}")
            return

        print(f"[2/3] 筛选 pipeline: {sub.name} ...")
        cfg = PipelineConfig(use_batch=use_batch)
        digest = await run_pipeline(session, sub, cfg=cfg)
        await session.commit()
        # 用 count 查询取条目数，避免 commit 后惰性加载关系（async 下会 MissingGreenlet）
        n_items = (
            await session.execute(
                select(func.count(DigestItem.id)).where(
                    DigestItem.digest_id == digest.id
                )
            )
        ).scalar_one()
        print(f"  digest#{digest.id} status={digest.status} items={n_items}")

        if not deliver:
            print("[3/3] 跳过投递（--no-deliver）")
            return
        if digest.status not in ("ready",):
            print(f"[3/3] digest 状态 {digest.status}，不投递")
            return

        print("[3/3] 投递 ...")
        logs = await deliver_digest(session, digest, settings)
        await session.commit()
        for log in logs:
            print(f"  {log.channel}: {log.status}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("subscription_id", type=int)
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--no-deliver", dest="deliver", action="store_false")
    p.add_argument("--batch", action="store_true")
    args = p.parse_args()
    asyncio.run(
        main(args.subscription_id, args.fetch, args.deliver, args.batch)
    )
