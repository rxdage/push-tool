"""推送自愈：判断今天该推的是否已成功投递、失败告警。

watchdog 的编排在 scheduler.py（那里能直接调 run_subscription_job /
daily_academic_company_job / deliver_digest，避免循环 import）；
本模块只放可独立测试的纯查询逻辑 + 告警 + 进程内去重。
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import anthropic
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.delivery.base import COMPANY_CHANNEL, PERSONAL_CHANNEL  # noqa: F401 — 转出给 scheduler
from app.delivery.feishu_bot import FeishuBot
from app.models import DeliveryLog, Digest

# 到点后留出缓冲再判定"漏推"，避免和正在跑的真任务抢跑导致重复推送
HEAL_GRACE = timedelta(minutes=15)

# 进程内去重（重启重置，可接受）：当天已告警的 push key
_alerted: set[str] = set()

# cron 星期编号(0/7=周日, 1=周一…6=周六) → apscheduler 星期名
_CRON_DOW = {
    "0": "sun", "1": "mon", "2": "tue", "3": "wed",
    "4": "thu", "5": "fri", "6": "sat", "7": "sun",
}


def _dow_cron_to_aps(field: str) -> str:
    """把 cron 的星期字段翻成 apscheduler 能正确理解的星期名。"""
    if field == "*":
        return "*"
    out: list[str] = []
    for tok in field.split(","):
        if "-" in tok:
            a, b = tok.split("-")
            out.append(f"{_CRON_DOW[a]}-{_CRON_DOW[b]}")
        else:
            out.append(_CRON_DOW[tok])
    return ",".join(out)


def cron_trigger(cron: str, tz: ZoneInfo) -> CronTrigger:
    """按标准 cron 语义正确构造 CronTrigger。

    apscheduler 的 from_crontab 不转换星期编号——cron 里 0=周日，而 apscheduler
    原生 0=周一——直接套用会把所有带星期的任务整体偏一天。本函数显式翻译星期字段。
    """
    m, h, dom, mon, dow = cron.split()
    return CronTrigger(
        minute=m, hour=h, day=dom, month=mon,
        day_of_week=_dow_cron_to_aps(dow), timezone=tz,
    )


def cron_fired_today(cron: str, tz: ZoneInfo, now: datetime) -> datetime | None:
    """若该 cron 今天（tz）有一个已过去 HEAL_GRACE 的触发点，返回它；否则 None。"""
    today = now.date()
    start = datetime.combine(today, dtime.min, tzinfo=tz)
    try:
        trig = cron_trigger(cron, tz)
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


def heal_action(digest: Digest | None, delivered: bool) -> str | None:
    """今天这期怎么补：None=已投成不用管；"rerun"=整套重跑；"redeliver"=只重投。

    没生成、或那期是 failed（抓取/摘要因网络全挂，一条内容都没有）都得重新抓取+摘要，
    重投一个空壳没有意义。
    """
    if delivered:
        return None
    if digest is None or digest.status == "failed":
        return "rerun"
    return "redeliver"


def failure_reason(digest_status: str | None) -> str:
    """补救后仍没投成的原因，写进告警给人看。"""
    if digest_status == "failed":
        return (
            "抓取/摘要全部失败，多半是 VPN（Clash）节点挂了、连不上 Claude API；"
            "推送时段内每 15 分钟自动重试，补上后会再通知"
        )
    if digest_status is None:
        return "今天的 digest 没生成出来"
    return "digest 已生成，但飞书投递失败"


async def llm_reachable(settings: Settings, timeout: float = 10.0) -> bool:
    """此刻能否连上 Claude API：收到任何 HTTP 响应（含 401/404）都算网络通。

    容器出海走 Windows 系统代理 → Clash → 节点，节点一挂这里就连接失败/超时/TLS EOF。
    用来区分"0 条是因为今天确实没新内容"（通）和"0 条是因为网络断了"（不通）。
    """
    try:
        async with anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or "probe", max_retries=0, timeout=timeout
        ) as client:
            await client.models.list(limit=1)
    except anthropic.APIConnectionError:  # 含超时
        return False
    except anthropic.APIStatusError:
        pass  # 拿到了 HTTP 响应，网络是通的
    return True


def should_alert(key: str) -> bool:
    """每个 (日期+push) 当天只告警一次。"""
    if key in _alerted:
        return False
    _alerted.add(key)
    return True


def was_alerted(key: str) -> bool:
    """今天是否已为该 push 发过告警（进程内，重启重置）。"""
    return key in _alerted


_VPN_HINT = (
    "VPN 节点挂了怎么办：打开 Clash Verge →「代理」→ XBoard，换一个非香港节点"
    "（香港节点用不了 Claude API）。仍不行可让 Claude 介入排查。"
)


def alert_body(lines: list[str]) -> str:
    body = "以下推送没能成功，请留意（可让 Claude 介入排查）：\n\n" + "\n".join(
        f"- {x}" for x in lines
    )
    if any("VPN" in x for x in lines):
        body += "\n\n" + _VPN_HINT
    return body


async def _send_ops(
    settings: Settings, title: str, body: str, what: str, lines: list[str]
) -> None:
    """发运维消息到个人群（尽力而为，失败只打日志）。

    情报内容已全部改推公司群，个人群 webhook 只剩运维通知这一个用途——
    这样排查噪音不会打扰公司群。飞书国内直连，VPN 挂了也发得出去。
    """
    if not settings.feishu_webhook_url or not lines:
        return
    try:
        await FeishuBot(
            settings.feishu_webhook_url, settings.feishu_webhook_secret
        ).send_markdown(title, body)
        print(f"[selfheal] 已发{what}: {lines}")
    except Exception as e:  # noqa: BLE001
        print(f"[selfheal] {what}发送也失败: {e}")


async def send_alert(settings: Settings, lines: list[str]) -> None:
    """补救后仍失败的推送 → 告警到个人群。"""
    await _send_ops(settings, "⚠️ 推送自愈告警", alert_body(lines), "告警", lines)


async def send_recovered(settings: Settings, lines: list[str]) -> None:
    """告警过的推送后来补推成功 → 发恢复通知，免得人一直悬着。"""
    body = "以下推送之前告警过，现已自动补推成功，无需处理：\n\n" + "\n".join(
        f"- {x}" for x in lines
    )
    await _send_ops(settings, "✅ 推送已恢复", body, "恢复通知", lines)
