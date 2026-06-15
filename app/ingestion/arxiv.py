"""arXiv API 适配器。

API: http://export.arxiv.org/api/query （Atom 输出，用 feedparser 解析）
config:
  categories: [cond-mat.mes-hall, ...]
  query: 关键词布尔串（可选）
  max_results: 默认 80
礼貌：arXiv 建议请求间隔 ~3s；单次拉取已足够，无需翻页时不连发。
"""
from __future__ import annotations

import asyncio

import feedparser
import httpx

from app.ingestion.base import RawItem, SourceAdapter, struct_time_to_dt

ARXIV_API = "https://export.arxiv.org/api/query"


class ArxivAdapter(SourceAdapter):
    kind = "arxiv"

    def _build_search_query(self) -> str:
        cats = self.config.get("categories") or []
        cat_clause = " OR ".join(f"cat:{c}" for c in cats)
        kw = (self.config.get("query") or "").strip()
        parts = []
        if cat_clause:
            parts.append(f"({cat_clause})")
        if kw:
            parts.append(f"(all:{kw})")
        return " AND ".join(parts) if parts else "all:nanopore"

    async def fetch(self) -> list[RawItem]:
        params = {
            "search_query": self._build_search_query(),
            "start": 0,
            "max_results": int(self.config.get("max_results", 80)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(ARXIV_API, params=params)
            resp.raise_for_status()
            text = resp.text

        parsed = await asyncio.to_thread(feedparser.parse, text)
        items: list[RawItem] = []
        for e in parsed.entries:
            # arxiv id 形如 http://arxiv.org/abs/2401.01234v1
            ext = e.get("id")
            abs_url = ext
            published = struct_time_to_dt(
                e.get("published_parsed") or e.get("updated_parsed")
            )
            authors = [a.get("name") for a in e.get("authors", []) if a.get("name")]
            items.append(
                RawItem(
                    external_id=ext,
                    url=abs_url,
                    title=(e.get("title") or "").replace("\n", " ").strip(),
                    abstract=(e.get("summary") or "").strip(),
                    published_at=published,
                    tags=[t.get("term") for t in e.get("tags", []) if t.get("term")],
                    raw_json={"source_name": self.name, "authors": authors},
                )
            )
        return items
