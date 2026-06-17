"""飞书自定义机器人 webhook（消息卡片 interactive，schema 2.0）。

签名算法（open.feishu.cn）：key = f"{timestamp}\\n{secret}"，对空串做 HmacSHA256，再 base64。
请求体 ≤ 20KB：超了把 card.body.elements 拆成多条卡片分发。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

import httpx

from app.config import settings as _settings
from app.delivery.base import DeliveryChannel
from app.delivery.formatter import DigestView, build_feishu_card

# 留余量，飞书上限 20KB
MAX_BODY_BYTES = 18000
# 可重试的飞书业务错误码（频率限制）
RETRYABLE_FEISHU_CODES = {11232}


def gen_sign(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _el_bytes(el: dict) -> int:
    return len(json.dumps(el, ensure_ascii=False).encode("utf-8"))


def _split_oversized_elements(elements: list[dict], content_budget: int) -> list[dict]:
    """把单个超大的 markdown 元素按行拆成多个小元素，保证可被分片。"""
    out: list[dict] = []
    for el in elements:
        if el.get("tag") != "markdown" or _el_bytes(el) <= content_budget:
            out.append(el)
            continue
        lines = el["content"].split("\n")
        buf: list[str] = []
        size = 0
        for line in lines:
            lb = len(line.encode("utf-8")) + 1
            if buf and size + lb > content_budget:
                out.append({"tag": "markdown", "content": "\n".join(buf)})
                buf, size = [], 0
            buf.append(line)
            size += lb
        if buf:
            out.append({"tag": "markdown", "content": "\n".join(buf)})
    return out


def _chunk_cards(card: dict, max_bytes: int = MAX_BODY_BYTES) -> list[dict]:
    """若整卡过大，按 elements 拆成多张卡片（保留同一 header）。"""
    full = json.dumps(card, ensure_ascii=False).encode("utf-8")
    if len(full) <= max_bytes:
        return [card]

    header = card.get("header")
    elements = card.get("body", {}).get("elements", [])
    # 先把超大单元素按行拆小，再按元素装片
    elements = _split_oversized_elements(elements, max_bytes - 800)
    cards: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            cards.append(
                {"schema": "2.0", "header": header, "body": {"elements": list(cur)}}
            )

    base = len(json.dumps({"schema": "2.0", "header": header, "body": {"elements": []}},
                          ensure_ascii=False).encode("utf-8"))
    size = base
    for el in elements:
        el_bytes = len(json.dumps(el, ensure_ascii=False).encode("utf-8")) + 1
        if cur and size + el_bytes > max_bytes:
            flush()
            cur = []
            size = base
        cur.append(el)
        size += el_bytes
    flush()
    return cards or [card]


def build_markdown_card(title: str, body_md: str) -> dict:
    """把一段 markdown 正文包成 schema 2.0 卡片（过长由 _chunk_cards 自动分片）。"""
    return {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": title}},
        "body": {"elements": [{"tag": "markdown", "content": body_md}]},
    }


class FeishuBot(DeliveryChannel):
    kind = "feishu_bot"

    def __init__(
        self,
        webhook_url: str,
        secret: str = "",
        timeout: float = 15.0,
        *,
        retry_attempts: int | None = None,
        retry_base_delay: float | None = None,
        inter_card_delay: float = 0.5,
    ):
        if not webhook_url:
            raise ValueError("FEISHU_WEBHOOK_URL 未配置")
        self.webhook_url = webhook_url
        self.secret = secret
        self.timeout = timeout
        self.retry_attempts = (
            retry_attempts
            if retry_attempts is not None
            else _settings.delivery_retry_attempts
        )
        self.retry_base_delay = (
            retry_base_delay
            if retry_base_delay is not None
            else _settings.delivery_retry_base_delay
        )
        self.inter_card_delay = inter_card_delay

    def _payload(self, card: dict) -> dict:
        payload = {"msg_type": "interactive", "card": card}
        if self.secret:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = gen_sign(ts, self.secret)
        return payload

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> dict:
        """发送一张卡片；限流(11232)/网络错误/5xx 自动退避重试。"""
        attempts = max(1, self.retry_attempts)
        last: Exception | None = None
        for i in range(attempts):
            if i:
                delay = self.retry_base_delay * (2 ** (i - 1))
                print(f"  ↻ 投递重试 {i}/{attempts - 1}，{delay:.0f}s 后再试…（{last}）")
                await asyncio.sleep(delay)
            try:
                resp = await client.post(self.webhook_url, json=payload)
            except httpx.HTTPError as e:
                last = RuntimeError(f"网络错误: {e}")
                continue
            if resp.status_code >= 500:
                last = RuntimeError(f"飞书 HTTP {resp.status_code}")
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPError as e:  # 4xx：客户端错误，重试无意义
                raise RuntimeError(f"飞书 HTTP 错误: {e}") from e
            data = resp.json()
            # 成功：code==0（新）或 StatusCode==0（旧）
            if data.get("code") == 0 or data.get("StatusCode") == 0:
                return data
            # 业务错误：限流可重试，其余直接抛
            if data.get("code") in RETRYABLE_FEISHU_CODES:
                last = RuntimeError(f"飞书限流: {data}")
                continue
            raise RuntimeError(f"飞书返回错误: {data}")
        raise last or RuntimeError("飞书发送失败（重试用尽）")

    async def send(self, view: DigestView) -> dict:
        card = build_feishu_card(view)
        return await self._send_card(card)

    async def send_markdown(self, title: str, body_md: str) -> dict:
        """发一段 markdown 正文（综述用）。"""
        return await self._send_card(build_markdown_card(title, body_md))

    async def _send_card(self, card: dict) -> dict:
        cards = _chunk_cards(card)
        responses = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for i, c in enumerate(cards):
                if i and self.inter_card_delay:
                    await asyncio.sleep(self.inter_card_delay)  # 多卡片间隔，降限流概率
                responses.append(await self._post(client, self._payload(c)))
        return {"status": "ok", "parts": len(cards), "response": responses}
