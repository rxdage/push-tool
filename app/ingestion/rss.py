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


class RssAdapter(SourceAdapter):
    kind = "rss"

    async def fetch(self) -> list[RawItem]:
        url = self.config.get("url")
        if not url:
            raise ValueError(f"rss source {self.name!r} 缺少 config.url")
        # feedparser 是同步的，丢线程池
        parsed = await asyncio.to_thread(feedparser.parse, url)
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
