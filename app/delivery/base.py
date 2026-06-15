"""DeliveryChannel ABC：send(view) -> 结果 dict（status/response）。

Phase-2 加企业微信/服务号/邮件只实现这个接口，不动 pipeline。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.delivery.formatter import DigestView


class DeliveryChannel(ABC):
    kind: str = "base"

    @abstractmethod
    async def send(self, view: DigestView) -> dict:
        """投递一个 digest 视图，返回 {status, response}。失败应抛异常或返回 status=error。"""
        raise NotImplementedError
