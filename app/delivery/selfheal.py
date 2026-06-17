"""推送自愈：判断今天该推的是否已成功投递、失败告警。

watchdog 的编排在 scheduler.py（那里能直接调 run_subscription_job /
daily_academic_company_job / deliver_digest，避免循环 import）；
本模块只放可独立测试的纯查询逻辑 + 告警 + 进程内去重。
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.delivery.feishu_bot import FeishuBot
from app.models import DeliveryLog, Digest

PERSONAL_CHANNEL = "feishu_bot"
COMPANY_CHANNEL = "feishu_company"

# 到点后留出缓冲再判定"漏推"，避免和正在跑的真任务抢跑导致重复推送
HEAL_GRACE = timedelta(minutes=15)

# 进程内去重（重启重置，可接受）：当天已告警的 push key
_alerted: set[str] = set()


def cron_fired_today(cron: str, tz: ZoneInfo, now: datetime) -> datetime | None:
    """若该 cron 今天（tz）有一个已过去 HEAL_GRACE 的触发点，返回它；否则 None。"""
    today = now.date()
    start = datetime.combine(today, dtime.min, tzinfo=tz)
    try:
        trig = CronTrigger.from_crontab(cron, timezone=tz)
    except Exception:  # noqa: BLE001 — cron 非法就当今天不该触发
        return None
    nxt = trig.get_next_fire_time(None, start)
    if nxt and nxt.astimezone(tz).date() == today and nxt + HEAL_GRACE <= now:
        return nxt
    return None


async def todays_digest(
    session: AsyncSession, sub_id: int, tz: ZoneInfo
) -> Digest | None:
    """该订阅今天（tz）生成的 digest（最近一条）。"""
    today = datetime.now(tz).date()
    rows = (
        await session.execute(
            select(Digest)
            .where(Digest.subscription_id == sub_id)
            .order_by(Digest.run_at.desc())
            .limit(10)
        )
    ).scalars().all()
    for d in rows:
        if d.run_at and d.run_at.astimezone(tz).date() == today:
            return d
    return None


async def delivered_ok(
    session: AsyncSession, digest_id: int, channel: str
) -> bool:
    row = (
        await session.execute(
            select(DeliveryLog.id)
            .where(
                DeliveryLog.digest_id == digest_id,
                DeliveryLog.channel == channel,
                DeliveryLog.status == "ok",
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def company_pushed_ok_today(session: AsyncSession, tz: ZoneInfo) -> bool:
    """今天（tz）是否有成功的公司群投递记录。"""
    today = datetime.now(tz).date()
    rows = (
        await session.execute(
            select(DeliveryLog.sent_at)
            .where(
                DeliveryLog.channel == COMPANY_CHANNEL,
                DeliveryLog.status == "ok",
            )
            .order_by(DeliveryLog.sent_at.desc())
            .limit(20)
        )
    ).scalars().all()
    return any(s and s.astimezone(tz).date() == today for s in rows)


def should_alert(key: str) -> bool:
    """每个 (日期+push) 当天只告警一次。"""
    if key in _alerted:
        return False
    _alerted.add(key)
    return True


async def send_alert(settings: Settings, lines: list[str]) -> None:
    """把多次补救仍失败的推送告警发到个人群（尽力而为，失败只打日志）。"""
    if not settings.feishu_webhook_url or not lines:
        return
    body = "以下推送多次重试仍失败，请人工检查（可让 Claude 介入排查）：\n\n" + "\n".join(
        f"- {x}" for x in lines
    )
    try:
        await FeishuBot(
            settings.feishu_webhook_url, settings.feishu_webhook_secret
        ).send_markdown("⚠️ 推送自愈告警", body)
        print(f"[selfheal] 已发告警: {lines}")
    except Exception as e:  # noqa: BLE001
        print(f"[selfheal] 告警发送也失败: {e}")
