"""通用 RSS/Atom 适配器（feedparser）。

config:
  url: feed 地址（必填）
  name: 显示名
  max_results: 截断（默认 60）
"""
from __future__ import annotations

import asyncio

import feedparser

from app.ingestion.base import RawItem, SourceAdapter, struct_time_to_dt

# feedparser 走裸 urllib，默认 UA 容易被 CDN/反爬当机器人秒断连接；带个正常浏览器 UA
# 能绕过这类识别——但代价是有些站换了 UA 后不再"秒拒"，而是直接不回应，feedparser/
# urllib 本身不设超时会一直硬等（曾实测卡住数分钟）。所以必须配合显式超时使用。
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) push-tool/0.1 (personal digest bot)"
FETCH_TIMEOUT_S = 15


class RssAdapter(SourceAdapter):
    kind = "rss"

    async def _parse_with_retry(self, url: str, attempts: int = 3):
        """feed 抓取对网络抖动重试（指数退避），和 html 适配器保持一致的韧性。

        feedparser 网络失败时通常不抛异常而是把 bozo_exception 挂在结果上，
        entries 为空——这里两种情况（抛异常 / bozo 但 0 条）都重试。每次尝试都套
        硬超时，防止某个源没有正确关闭连接时把整条 ingest pipeline 卡死。
        """
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                parsed = await asyncio.wait_for(
                    asyncio.to_thread(feedparser.parse, url, agent=USER_AGENT),
                    timeout=FETCH_TIMEOUT_S,
                )
                if parsed.entries or not parsed.bozo:
                    return parsed
                last_exc = parsed.get("bozo_exception") or RuntimeError(
                    "feedparser: 0 条且 bozo=1"
                )
            except asyncio.TimeoutError:
                last_exc = TimeoutError(f"feedparser 超过 {FETCH_TIMEOUT_S}s 未响应")
            except Exception as e:  # noqa: BLE001
                last_exc = e
            if i < attempts - 1:
                await asyncio.sleep(2**i)  # 1s, 2s
        raise last_exc  # type: ignore[misc]

    async def fetch(self) -> list[RawItem]:
        url = self.config.get("url")
        if not url:
            raise ValueError(f"rss source {self.name!r} 缺少 config.url")
        parsed = await self._parse_with_retry(url)
        max_results = int(self.config.get("max_results", 60))

        items: list[RawItem] = []
        for e in parsed.entries[:max_results]:
            link = e.get("link")
            summary = e.get("summary") or e.get("description")
            published = struct_time_to_dt(
                e.get("published_parsed") or e.get("updated_parsed")
            )
            items.append(
                RawItem(
                    external_id=e.get("id") or link,
                    url=link,
                    title=(e.get("title") or "").strip(),
                    abstract=summary,
                    published_at=published,
                    tags=[t.get("term") for t in e.get("tags", []) if t.get("term")],
                    raw_json={"source_name": self.name},
                )
            )
        return items
