"""投递编排：digest -> 各渠道 send -> 记 DeliveryLog。

2026-08 起只推公司群（FEISHU_WEBHOOK_URL_COMPANY）：个人群不再收任何情报内容，
个人群 webhook 只留给 selfheal 的"推送失败"运维告警。
Phase-2 改读 Channel 表即可支持多群路由。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings as default_settings
from app.delivery.base import COMPANY_CHANNEL, DeliveryChannel
from app.delivery.feishu_bot import FeishuBot
from app.delivery.formatter import build_view
from app.models import DeliveryLog, Digest


def build_channels(settings: Settings) -> list[DeliveryChannel]:
    """所有订阅的 digest 都投公司群，个人群不再投内容。"""
    channels: list[DeliveryChannel] = []
    if settings.feishu_webhook_url_company:
        channels.append(
            FeishuBot(
                settings.feishu_webhook_url_company,
                settings.feishu_webhook_secret_company,
                kind=COMPANY_CHANNEL,
            )
        )
    return channels


async def deliver_digest(
    session: AsyncSession,
    digest: Digest,
    settings: Settings | None = None,
) -> list[DeliveryLog]:
    settings = settings or default_settings
    view = await build_view(session, digest)
    channels = build_channels(settings)
    logs: list[DeliveryLog] = []

    if not channels:
        print("  ! 未配置公司群 webhook（FEISHU_WEBHOOK_URL_COMPANY 为空），跳过投递。")
        return logs

    if view.total == 0:
        # 本期没有命中条目：不发"空卡片"骚扰群里，但仍记一条 ok 日志——
        # 否则自愈 watchdog 会一直判定"今天没推成功"，每 15 分钟重跑一次。
        print("  · 本期 0 条命中，跳过投递（仅记录）。")
        for ch in channels:
            log = DeliveryLog(
                digest_id=digest.id,
                channel=ch.kind,
                status="ok",
                response={"skipped": "empty digest, no push"},
            )
            session.add(log)
            logs.append(log)
        await session.flush()
        return logs

    for ch in channels:
        try:
            result = await ch.send(view)
            status = result.get("status", "ok")
            response = result
        except Exception as e:  # noqa: BLE001
            status = "error"
            response = {"error": str(e)}
            print(f"  ! 渠道 {ch.kind} 投递失败: {e}")
        log = DeliveryLog(
            digest_id=digest.id, channel=ch.kind, status=status, response=response
        )
        session.add(log)
        logs.append(log)

    await session.flush()
    return logs
