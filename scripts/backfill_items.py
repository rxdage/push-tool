"""一次性补推：把指定的几条 DigestItem 单独发一张卡片到公司群。

口径切换（2026-08-09）当晚有两条只进了个人群、没进公司群，用这个补上。
复用已生成好的摘要，0 LLM 调用。

用法（在项目根目录）：
    docker compose run --rm app python scripts/backfill_items.py --dry-run
    docker compose run --rm app python scripts/backfill_items.py

PICKS 里是 (digest_id, bucket, rank)，和 digest_items 表一一对应。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 直接 `python scripts/xxx.py` 时 sys.path[0] 是 scripts/，把项目根加进来
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings
from app.db import SessionLocal
from app.delivery.base import COMPANY_CHANNEL
from app.delivery.feishu_bot import FeishuBot
from app.delivery.formatter import DigestView, ItemView, build_feishu_card
from app.models import DeliveryLog, DigestItem, Item

CARD_TITLE = "情报补录"
# (digest_id, bucket, rank, 标题覆盖)
# 标题覆盖：CleanSin 的 html 抓取把整页正文塞进了 title，原样发出去很难看，这里手工修一条。
PICKS = [
    (85, "deep", 1, "Why You Should Never Ultrasonically Clean SiN Windows (3 Cardinal Sins) — CleanSiN"),
    (84, "brief", 0, None),
]


async def main(dry_run: bool) -> None:
    date_str = datetime.now(ZoneInfo(settings.tz_default)).strftime("%Y-%m-%d")
    view = DigestView(subscription_name=CARD_TITLE, date_str=date_str)
    by_digest: dict[int, list[int]] = {}

    async with SessionLocal() as session:
        for digest_id, bucket, rank, title_override in PICKS:
            row = (
                await session.execute(
                    select(DigestItem, Item)
                    .join(Item, DigestItem.item_id == Item.id)
                    .where(
                        DigestItem.digest_id == digest_id,
                        DigestItem.bucket == bucket,
                        DigestItem.rank == rank,
                    )
                )
            ).first()
            if row is None:
                raise SystemExit(f"找不到 digest#{digest_id} {bucket}#{rank}")
            di, it = row
            getattr(view, bucket).append(
                ItemView(
                    title=title_override or it.title or "(无标题)",
                    url=it.url,
                    summary=di.summary,
                    classification=di.classification,
                    action_label=di.action_label,
                    why_it_matters=di.why_it_matters,
                )
            )
            by_digest.setdefault(digest_id, []).append(it.id)

        card = build_feishu_card(view)
        if dry_run:
            print(f"[dry-run] {view.total} 条，卡片预览：")
            print(json.dumps(card, ensure_ascii=False, indent=2))
            return

        bot = FeishuBot(
            settings.feishu_webhook_url_company,
            settings.feishu_webhook_secret_company,
            kind=COMPANY_CHANNEL,
        )
        result = await bot.send(view)
        for digest_id, item_ids in by_digest.items():
            session.add(
                DeliveryLog(
                    digest_id=digest_id,
                    channel=COMPANY_CHANNEL,
                    status=result.get("status", "ok"),
                    response={
                        "item_ids": item_ids,
                        "parts": result.get("parts"),
                        "source": "backfill-2026-08-09",
                    },
                )
            )
        await session.commit()
        print(f"已推送 {view.total} 条到公司群，记录 {len(by_digest)} 条 DeliveryLog。")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    asyncio.run(main(p.parse_args().dry_run))
