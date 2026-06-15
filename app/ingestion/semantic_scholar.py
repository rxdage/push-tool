"""Semantic Scholar (S2) Graph API 适配器。

拿引用数识别经典/影响力。
config:
  query: 检索词（必填）
  fields: 返回字段，默认含 citationCount
  limit: 默认 60
S2_API_KEY（settings.s2_api_key）可选，填了提高限速。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.ingestion.base import RawItem, SourceAdapter

S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
DEFAULT_FIELDS = ["title", "abstract", "year", "citationCount", "externalIds", "url"]


class SemanticScholarAdapter(SourceAdapter):
    kind = "s2"

    async def fetch(self) -> list[RawItem]:
        query = self.config.get("query")
        if not query:
            raise ValueError(f"s2 source {self.name!r} 缺少 config.query")
        fields = self.config.get("fields") or DEFAULT_FIELDS
        limit = int(self.config.get("limit", 60))

        headers = {}
        api_key = getattr(self.settings, "s2_api_key", "") if self.settings else ""
        if api_key:
            headers["x-api-key"] = api_key

        params = {"query": query, "fields": ",".join(fields), "limit": limit}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            # 无 key 时 S2 共享限速易 429；退避重试几次。
            data = []
            for i in range(4):
                resp = await client.get(S2_SEARCH, params=params, headers=headers)
                if resp.status_code in (429, 500, 502, 503) and i < 3:
                    wait = float(resp.headers.get("retry-after", 2 ** i))
                    await asyncio.sleep(min(wait, 10))
                    continue
                resp.raise_for_status()
                data = resp.json().get("data", [])
                break

        items: list[RawItem] = []
        for p in data:
            ext_ids = p.get("externalIds") or {}
            doi = ext_ids.get("DOI")
            paper_id = p.get("paperId")
            year = p.get("year")
            published = (
                datetime(int(year), 1, 1, tzinfo=timezone.utc) if year else None
            )
            citation_count = p.get("citationCount")
            tags = []
            if isinstance(citation_count, int) and citation_count >= 100:
                tags.append("high-citation")
            items.append(
                RawItem(
                    external_id=f"doi:{doi}" if doi else (f"s2:{paper_id}" if paper_id else None),
                    url=p.get("url"),
                    title=(p.get("title") or "").strip(),
                    abstract=p.get("abstract"),
                    published_at=published,
                    tags=tags,
                    raw_json={
                        "source_name": self.name,
                        "citationCount": citation_count,
                        "externalIds": ext_ids,
                    },
                )
            )
        return items
