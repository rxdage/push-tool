"""手工补推单条：把一条 item 用指定画像摘要后并入某期 digest，并推公司群。

用于"当天窄口径 0 条命中、但确实有一条值得发"的情况。会写 DigestItem —— 这样
跨期去重认得它，以后不会再推第二遍。

    docker compose run --rm app python scripts/push_single_item.py <item_id> <profile_id> <digest_id> [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.curation.summarize import DEFAULT_MODEL, summarize_items  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.delivery.base import COMPANY_CHANNEL  # noqa: E402
from app.delivery.feishu_bot import FeishuBot  # noqa: E402
from app.delivery.formatter import (  # noqa: E402
    DigestView,
    ItemView,
    build_feishu_card,
)
from app.models import (  # noqa: E402
    DeliveryLog,
    Digest,
    DigestItem,
    InterestProfile,
    Item,
    Subscription,
)


async def main(item_id: int, profile_id: int, digest_id: int, dry_run: bool) -> None:
    async with SessionLocal() as s:
        it = await s.get(Item, item_id)
        prof = await s.get(InterestProfile, profile_id)
        digest = await s.get(Digest, digest_id)
        sub = await s.get(Subscription, digest.subscription_id)
        if it is None or prof is None or digest is None:
            raise SystemExit("item / profile / digest 不存在")

        dup = (
            await s.execute(
                select(DigestItem.id).where(
                    DigestItem.digest_id == digest_id, DigestItem.item_id == item_id
                )
            )
        ).first()
        if dup is not None:
            raise SystemExit(f"item#{item_id} 已在 digest#{digest_id} 里，避免重复推送")

        res = await summarize_items([it], prof, sub.feed_type, DEFAULT_MODEL)
        r = res.get(it.id)
        if r is None:
            raise SystemExit("摘要生成失败")

        date_str = datetime.now(ZoneInfo(settings.tz_default)).strftime("%Y-%m-%d")
        view = DigestView(subscription_name=sub.name, date_str=date_str)
        view.deep.append(
            ItemView(
                title=it.title or "(无标题)",
                url=it.url,
                summary=r["summary"],
                classification=r["classification"],
                action_label=r["action_label"],
                why_it_matters=r["why_it_matters"],
            )
        )

        if dry_run:
            print(f"[dry-run] relevance={r['relevance']}")
            print(json.dumps(build_feishu_card(view), ensure_ascii=False, indent=2))
            return

        next_rank = (
            await s.execute(
                select(func.coalesce(func.max(DigestItem.rank), -1) + 1).where(
                    DigestItem.digest_id == digest_id, DigestItem.bucket == "deep"
                )
            )
        ).scalar_one()
        s.add(
            DigestItem(
                digest_id=digest_id,
                item_id=it.id,
                bucket="deep",
                rank=next_rank,
                summary=r["summary"],
                classification=r["classification"],
                action_label=r["action_label"],
                why_it_matters=r["why_it_matters"],
            )
        )

        bot = FeishuBot(
            settings.feishu_webhook_url_company,
            settings.feishu_webhook_secret_company,
            kind=COMPANY_CHANNEL,
        )
        result = await bot.send(view)
        s.add(
            DeliveryLog(
                digest_id=digest_id,
                channel=COMPANY_CHANNEL,
                status=result.get("status", "ok"),
                response={
                    "item_ids": [it.id],
                    "parts": result.get("parts"),
                    "source": "manual-single-backfill",
                    "profile_used": prof.name,
                },
            )
        )
        await s.commit()
        print(f"已推送 item#{it.id} 到公司群，并写入 digest#{digest_id}（deep#{next_rank}）。")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("item_id", type=int)
    p.add_argument("profile_id", type=int)
    p.add_argument("digest_id", type=int)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    asyncio.run(main(a.item_id, a.profile_id, a.digest_id, a.dry_run))
