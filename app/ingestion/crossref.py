"""Crossref REST API 适配器：按 ISSN 拉某本期刊的最新论文。

用来替代出版社自家的 RSS。pubs.acs.org 的 feed 现在挂了 Cloudflare 人机验证
（403 + "Just a moment..." 挑战页，feedparser 拿到 HTML 直接解析失败），
Crossref 是公开的文献元数据 API，无需鉴权，拿同样的期刊目录。

config:
  issn: 期刊 ISSN（必填，用 online/electronic ISSN）
  name: 显示名
  max_results: 默认 40
  days: 只取最近 N 天（默认 30）

礼貌池：带 mailto（PUBMED_EMAIL）能拿到更宽松的限速。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.ingestion.base import RawItem, SourceAdapter

CROSSREF = "https://api.crossref.org/journals/{issn}/works"
# 摘要是 JATS XML（<jats:p>…</jats:p>），存库前把标签剥掉
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean_abstract(raw: str | None) -> str | None:
    if not raw:
        return None
    text = _WS.sub(" ", _TAG.sub(" ", raw)).strip()
    # Crossref 的摘要常以字面量 "Abstract" 开头，去掉
    if text.lower().startswith("abstract"):
        text = text[len("abstract"):].strip()
    return text or None


def _published(item: dict) -> datetime | None:
    for key in ("published", "published-online", "published-print", "issued"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            p = list(parts[0]) + [1, 1]
            try:
                return datetime(int(p[0]), int(p[1]), int(p[2]), tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
    return None


class CrossrefAdapter(SourceAdapter):
    kind = "crossref"

    async def fetch(self) -> list[RawItem]:
        issn = self.config.get("issn")
        if not issn:
            raise ValueError(f"crossref source {self.name!r} 缺少 config.issn")
        rows = int(self.config.get("max_results", 40))
        days = int(self.config.get("days", 30))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

        params = {
            "rows": rows,
            "sort": "published",
            "order": "desc",
            "filter": f"from-pub-date:{since}",
            "select": "DOI,title,abstract,published,URL,container-title",
        }
        email = getattr(self.settings, "pubmed_email", "") if self.settings else ""
        if email:
            params["mailto"] = email  # 礼貌池，限速更宽松
        headers = {"User-Agent": f"push-tool/0.1 (personal digest bot; mailto:{email or 'n/a'})"}

        url = CROSSREF.format(issn=issn)
        data: list[dict] = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for i in range(4):
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code in (429, 500, 502, 503) and i < 3:
                    wait = float(resp.headers.get("retry-after", 2**i))
                    await asyncio.sleep(min(wait, 10))
                    continue
                resp.raise_for_status()
                data = (resp.json().get("message") or {}).get("items") or []
                break

        items: list[RawItem] = []
        for p in data:
            titles = p.get("title") or []
            # ACS 等出版社在 Crossref 里的标题带硬换行，归一成单行
            title = _WS.sub(" ", (titles[0] if titles else "")).strip()
            if not title:
                continue
            doi = p.get("DOI")
            journal = (p.get("container-title") or [None])[0]
            items.append(
                RawItem(
                    external_id=f"doi:{doi}" if doi else None,
                    url=p.get("URL") or (f"https://doi.org/{doi}" if doi else None),
                    title=title,
                    abstract=_clean_abstract(p.get("abstract")),
                    published_at=_published(p),
                    raw_json={"source_name": self.name, "doi": doi, "journal": journal},
                )
            )
        return items
