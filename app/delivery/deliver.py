"""投递编排：digest -> 各渠道 send -> 记 DeliveryLog。

MVP 渠道走 env（FEISHU_WEBHOOK_URL）。Phase-2 改读 Channel 表即可。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings as default_settings
from app.delivery.base import DeliveryChannel
from app.delivery.feishu_bot import FeishuBot
from app.delivery.formatter import build_view
from app.models import DeliveryLog, Digest


def build_channels(settings: Settings) -> list[DeliveryChannel]:
    channels: list[DeliveryChannel] = []
    if settings.feishu_webhook_url:
        channels.append(
            FeishuBot(settings.feishu_webhook_url, settings.feishu_webhook_secret)
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
        print("  ! 未配置任何投递渠道（FEISHU_WEBHOOK_URL 为空），跳过投递。")
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
