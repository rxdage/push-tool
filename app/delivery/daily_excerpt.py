"""每日学术精选 → 公司群（飞书）。复用每周学术 digest 已筛好的条目，零额外 LLM 调用。

每天早 8 点从"最新一期学术周报 digest"里取尚未推过公司群的 N 条（深度优先），
推到公司群 webhook，把一周的精华摊到工作日早晨发——省 token、不重复、不影响个人群与行业日报。

用法（手动/测试）：
    python -m app.delivery.daily_excerpt --dry-run   # 只看选出哪几条，不发送
    python -m app.delivery.daily_excerpt             # 真发到公司群
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.config import settings as default_settings
from app.db import SessionLocal
from app.delivery.feishu_bot import FeishuBot
from app.delivery.formatter import DigestView, ItemView, build_feishu_card
from app.models import DeliveryLog, Digest, DigestItem, Item, Subscription

COMPANY_CHANNEL = "feishu_company"
# 深度优先：每天先发最强的
BUCKET_ORDER = {"deep": 0, "brief": 1, "classic": 2}


async def _latest_academic_digest(session: AsyncSession) -> Digest | None:
    sub = (
        await session.execute(
            select(Subscription)
            .where(Subscription.feed_type == "academic")
            .order_by(Subscription.id)
        )
    ).scalars().first()
    if sub is None:
        return None
    return (
        await session.execute(
            select(Digest)
            .where(Digest.subscription_id == sub.id, Digest.status == "ready")
            .order_by(desc(Digest.run_at))
        )
    ).scalars().first()


async def _pushed_item_ids(session: AsyncSession, digest_id: int) -> set[int]:
    """该期 digest 已经推过公司群的 item_id（记在 DeliveryLog.response 里）。"""
    rows = (
        await session.execute(
            select(DeliveryLog.response).where(
                DeliveryLog.digest_id == digest_id,
                DeliveryLog.channel == COMPANY_CHANNEL,
            )
        )
    ).scalars().all()
    ids: set[int] = set()
    for r in rows:
        for i in (r or {}).get("item_ids", []):
            ids.add(i)
    return ids


async def select_daily_items(
    session: AsyncSession, digest: Digest, n: int
) -> list[tuple[DigestItem, Item]]:
    pushed = await _pushed_item_ids(session, digest.id)
    rows = (
        await session.execute(
            select(DigestItem, Item)
            .join(Item, DigestItem.item_id == Item.id)
            .where(DigestItem.digest_id == digest.id)
        )
    ).all()
    fresh = [(di, it) for di, it in rows if it.id not in pushed]
    fresh.sort(key=lambda r: (BUCKET_ORDER.get(r[0].bucket, 9), r[0].rank))
    return fresh[:n]


def build_company_view(
    pairs: list[tuple[DigestItem, Item]], date_str: str
) -> DigestView:
    """公司群卡片：学术内容，无行动/关注标签，统一列成简报式。"""
    view = DigestView(subscription_name="学术精选 · 每日5条", date_str=date_str)
    for di, it in pairs:
        view.brief.append(
            ItemView(
                title=it.title or "(无标题)",
                url=it.url,
                summary=di.summary,
                classification=di.classification,
                action_label=None,
                why_it_matters=None,
            )
        )
    return view


async def push_daily_academic(
    session: AsyncSession,
    settings: Settings | None = None,
    n: int | None = None,
    dry_run: bool = False,
) -> dict:
    settings = settings or default_settings
    n = n or settings.daily_academic_count
    date_str = datetime.now(ZoneInfo(settings.tz_default)).strftime("%Y-%m-%d")

    if not settings.feishu_webhook_url_company and not dry_run:
        return {"status": "skip", "reason": "未配置 FEISHU_WEBHOOK_URL_COMPANY"}

    digest = await _latest_academic_digest(session)
    if digest is None:
        return {"status": "skip", "reason": "暂无 ready 的学术周报 digest"}

    pairs = await select_daily_items(session, digest, n)
    if not pairs:
        return {"status": "skip", "reason": "本期学术精华已发完，待下周日刷新"}

    view = build_company_view(pairs, date_str)
    item_ids = [it.id for _, it in pairs]
    titles = [it.title for _, it in pairs]

    if dry_run:
        return {
            "status": "dry",
            "from_digest": digest.id,
            "count": len(pairs),
            "titles": titles,
            "card": build_feishu_card(view),
        }

    bot = FeishuBot(
        settings.feishu_webhook_url_company, settings.feishu_webhook_secret_company
    )
    result = await bot.send(view)
    session.add(
        DeliveryLog(
            digest_id=digest.id,
            channel=COMPANY_CHANNEL,
            status=result.get("status", "ok"),
            response={"item_ids": item_ids, "parts": result.get("parts")},
        )
    )
    await session.flush()
    return {"status": "ok", "from_digest": digest.id, "sent": len(item_ids), "titles": titles}


async def _main(dry_run: bool) -> None:
    async with SessionLocal() as session:
        res = await push_daily_academic(session, default_settings, dry_run=dry_run)
        if not dry_run:
            await session.commit()
        if res.get("status") == "dry":
            print(f"[dry-run] 来自 digest#{res['from_digest']}，将发送 {res['count']} 条：")
            for i, t in enumerate(res["titles"], 1):
                print(f"  {i}. {t}")
        else:
            print(res)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(_main(args.dry_run))
