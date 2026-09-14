"""调度：每个 active subscription 一个 tz-aware cron job。

job = 抓取 → 筛选 pipeline → 投递。幂等（同一天同订阅不重复出 digest），记 run 状态。
预留切队列（Arq/Celery/RQ）的接缝：把 run_subscription_job 入队即可。

2026-08 起推送口径统一：所有订阅都只投公司群，时间表沿用原个人群那套
（行业日报 工作日 07:30 / 学术周报 周日 20:00 / 行业周报 周日 20:05 /
半月综述 1、15 号 09:00）。原来 12:40 的两个公司群专用任务（每日学术精选 5 条、
竞对窄口径行业日报）已取消——内容与上面这几条重复，"同一篇只推一次"。

standalone：python -m app.scheduler
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import settings
from app.curation.pipeline import run_pipeline
from app.db import SessionLocal
from app.delivery import selfheal
from app.delivery.deliver import deliver_digest
from app.delivery.review import push_review
from app.ingestion.run import run as run_ingest
from app.models import Digest, Subscription


async def _already_ran_today(session, sub: Subscription) -> bool:
    """幂等：今天（按订阅 tz）是否已生成过该订阅的 digest。"""
    tz = ZoneInfo(sub.tz or settings.tz_default)
    today = datetime.now(tz).date()
    rows = await session.execute(
        select(Digest.run_at).where(Digest.subscription_id == sub.id)
    )
    for (run_at,) in rows.all():
        if run_at and run_at.astimezone(tz).date() == today:
            return True
    return False


async def run_subscription_job(sub_id: int, *, fetch: bool = True) -> None:
    """一个订阅的完整运行。被 cron 触发，也可手动调。"""
    print(f"[job] subscription#{sub_id} 开始 {datetime.now().isoformat(timespec='seconds')}")
    if fetch:
        try:
            await run_ingest(sub_id)
        except Exception as e:  # noqa: BLE001 — 抓取失败不阻断后续（用已有 item）
            print(f"[job] 抓取异常（继续）: {e}")

    async with SessionLocal() as session:
        sub = await session.get(Subscription, sub_id)
        if sub is None or not sub.active:
            print(f"[job] subscription#{sub_id} 不存在或未激活，跳过")
            return
        if await _already_ran_today(session, sub):
            print(f"[job] subscription#{sub_id} 今天已运行过，幂等跳过")
            return

        digest = await run_pipeline(session, sub)
        await session.commit()
        print(f"[job] digest#{digest.id} status={digest.status}")

        if digest.status == "ready":
            await deliver_digest(session, digest, settings)
            await session.commit()
            print(f"[job] subscription#{sub_id} 投递完成")


async def biweekly_review_job() -> None:
    """每月 1 号、15 号：重生成学术+行业 skill，并把综述推到公司群。"""
    try:
        results = await push_review(settings)
        print(f"[半月综述] {results}")
    except Exception as e:  # noqa: BLE001
        print(f"[半月综述] 失败: {e}")


async def watchdog_job() -> None:
    """补推校验：核对今天该推的是否都成功投递；缺则补、补不上则告警。

    推送时段每 15 分钟跑一遍，幂等：已成功投递的直接跳过；只补真正漏掉/失败的。
    """
    tz = ZoneInfo(settings.tz_default)
    now = datetime.now(tz)
    healed: list[str] = []
    failed: list[str] = []

    async with SessionLocal() as session:
        subs = (
            await session.execute(
                select(Subscription).where(Subscription.active.is_(True))
            )
        ).scalars().all()

    # 各订阅（行业日报 / 学术周报 / 行业周报）→ 公司群
    for sub in subs:
        sub_tz = ZoneInfo(sub.tz or settings.tz_default)
        if selfheal.cron_fired_today(sub.schedule_cron, sub_tz, datetime.now(sub_tz)) is None:
            continue  # 今天本来就不该推（或还没到补救时间）
        async with SessionLocal() as session:
            digest = await selfheal.todays_digest(session, sub.id, sub_tz)
            already = digest is not None and await selfheal.delivered_ok(
                session, digest.id, selfheal.COMPANY_CHANNEL
            )
            digest_id = digest.id if digest else None
        if already:
            continue
        label = f"{sub.name}(公司群)"
        try:
            if digest_id is None:
                await run_subscription_job(sub.id)  # 没生成 → 整套重跑（含投递）
            else:
                async with SessionLocal() as session:  # 生成了但没投成 → 重投
                    d = await session.get(Digest, digest_id)
                    await deliver_digest(session, d, settings)
                    await session.commit()
            async with SessionLocal() as session:
                d2 = await selfheal.todays_digest(session, sub.id, sub_tz)
                ok = d2 is not None and await selfheal.delivered_ok(
                    session, d2.id, selfheal.COMPANY_CHANNEL
                )
            (healed if ok else failed).append(label)
        except Exception as e:  # noqa: BLE001
            print(f"[watchdog] {label} 补救失败: {e}")
            failed.append(label)

    if healed:
        print(f"[watchdog] 已补推: {healed}")
    if failed and settings.selfheal_alert:
        today = now.strftime("%Y-%m-%d")
        fresh = [f for f in failed if selfheal.should_alert(f"{today}:{f}")]
        if fresh:
            await selfheal.send_alert(settings, fresh)
    if not healed and not failed:
        print("[watchdog] 核对完成：今日推送均已就绪。")


async def load_jobs(scheduler: AsyncIOScheduler) -> int:
    """读所有 active subscription 注册 cron job（全部投公司群）+ 半月综述 + watchdog。"""
    async with SessionLocal() as session:
        subs = (
            await session.execute(
                select(Subscription).where(Subscription.active.is_(True))
            )
        ).scalars().all()

    for sub in subs:
        tz = ZoneInfo(sub.tz or settings.tz_default)
        trigger = selfheal.cron_trigger(sub.schedule_cron, tz)
        scheduler.add_job(
            run_subscription_job,
            trigger=trigger,
            args=[sub.id],
            id=f"sub-{sub.id}",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
        )
        print(f"[scheduler] + sub#{sub.id} {sub.name!r} cron={sub.schedule_cron!r} tz={sub.tz}")

    n_jobs = len(subs)

    if not settings.feishu_webhook_url_company:
        print("[scheduler] ! FEISHU_WEBHOOK_URL_COMPANY 为空——digest 会照常生成但不会投递。")

    # 每月 1 号、15 号 09:00 重生成 skill + 半月综述推公司群
    tz = ZoneInfo(settings.tz_default)
    scheduler.add_job(
        biweekly_review_job,
        trigger=CronTrigger(day="1,15", hour=9, minute=0, timezone=tz),
        id="biweekly-review",
        replace_existing=True,
        misfire_grace_time=6 * 3600,
        coalesce=True,
    )
    print(f"[scheduler] + 半月综述(skill+推公司群) cron='0 9 1,15 * *' tz={settings.tz_default}")
    n_jobs += 1

    # 推送自愈 watchdog：推送时段每 15 分钟核对、补推、失败告警（不计入订阅数）
    scheduler.add_job(
        watchdog_job,
        trigger=CronTrigger(minute="*/15", hour="7-14,20-22", timezone=tz),
        id="selfheal-watchdog",
        replace_existing=True,
        misfire_grace_time=600,
        coalesce=True,
    )
    print("[scheduler] + 推送自愈 watchdog cron='*/15 7-14,20-22 * * *'")

    return n_jobs


async def main() -> None:
    scheduler = AsyncIOScheduler()
    n = await load_jobs(scheduler)
    if n == 0:
        print("[scheduler] 没有 active 订阅，退出。")
        return
    scheduler.start()
    print(f"[scheduler] 已启动，{n} 个定时任务（含半月综述）。Ctrl-C 退出。")
    try:
        await asyncio.Event().wait()  # 阻塞
    except (KeyboardInterrupt, SystemExit):
        print("[scheduler] 停止。")


if __name__ == "__main__":
    asyncio.run(main())
