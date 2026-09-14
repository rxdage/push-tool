"""DeliveryChannel ABC：send(view) -> 结果 dict（status/response）。

Phase-2 加企业微信/服务号/邮件只实现这个接口，不动 pipeline。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.delivery.formatter import DigestView

# DeliveryLog.channel 取值。集中在这里，deliver / selfheal 共用。
#
# 2026-08 起：所有 digest 只投公司群（COMPANY_CHANNEL）。个人群不再收情报内容，
# PERSONAL_CHANNEL 只剩两个用途——读历史投递日志、以及 selfheal 的失败告警。
PERSONAL_CHANNEL = "feishu_bot"
COMPANY_CHANNEL = "feishu_company"
# 历史遗留：2026-08 之前公司群竞对窄口径日报单独用过这个 kind，只用于读旧日志。
LEGACY_COMPANY_INDUSTRY_CHANNEL = "feishu_company_industry"


class DeliveryChannel(ABC):
    kind: str = "base"

    @abstractmethod
    async def send(self, view: DigestView) -> dict:
        """投递一个 digest 视图，返回 {status, response}。失败应抛异常或返回 status=error。"""
        raise NotImplementedError
