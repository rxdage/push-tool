"""飞书自定义机器人 webhook（消息卡片 interactive，schema 2.0）。

签名算法（open.feishu.cn）：key = f"{timestamp}\\n{secret}"，对空串做 HmacSHA256，再 base64。
请求体 ≤ 20KB：超了把 card.body.elements 拆成多条卡片分发。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx

from app.delivery.base import DeliveryChannel
from app.delivery.formatter import DigestView, build_feishu_card

# 留余量，飞书上限 20KB
MAX_BODY_BYTES = 18000


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


class FeishuBot(DeliveryChannel):
    kind = "feishu_bot"

    def __init__(self, webhook_url: str, secret: str = "", timeout: float = 15.0):
        if not webhook_url:
            raise ValueError("FEISHU_WEBHOOK_URL 未配置")
        self.webhook_url = webhook_url
        self.secret = secret
        self.timeout = timeout

    def _payload(self, card: dict) -> dict:
        payload = {"msg_type": "interactive", "card": card}
        if self.secret:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = gen_sign(ts, self.secret)
        return payload

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> dict:
        resp = await client.post(self.webhook_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        # 成功：code==0（新）或 StatusCode==0（旧）
        ok = data.get("code") == 0 or data.get("StatusCode") == 0
        if not ok:
            raise RuntimeError(f"飞书返回错误: {data}")
        return data

    async def send(self, view: DigestView) -> dict:
        card = build_feishu_card(view)
        cards = _chunk_cards(card)
        responses = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for c in cards:
                responses.append(await self._post(client, self._payload(c)))
        return {"status": "ok", "parts": len(cards), "response": responses}
