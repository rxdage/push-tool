"""PubMed E-utilities 适配器（esearch -> efetch XML）。

config:
  term: 检索式（必填）
  max_results: 默认 60
礼貌：带 tool + email（PUBMED_EMAIL）；无 api_key 时限速 ~3 req/s，这里只发 2 个请求。
"""
from __future__ import annotations

import asyncio
from xml.etree import ElementTree as ET

import httpx

from app.ingestion.base import RawItem, SourceAdapter

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _pub_datetime(article: ET.Element):
    """从 <PubDate> 取年份（月/日不全时退回年初）。返回 tz-aware datetime 或 None。"""
    from datetime import datetime, timezone

    y = article.findtext(".//JournalIssue/PubDate/Year")
    if not y or not y.isdigit():
        return None
    months = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    m_raw = article.findtext(".//JournalIssue/PubDate/Month") or "1"
    m = months.get(m_raw, int(m_raw) if m_raw.isdigit() else 1)
    d_raw = article.findtext(".//JournalIssue/PubDate/Day") or "1"
    d = int(d_raw) if d_raw.isdigit() else 1
    try:
        return datetime(int(y), m, d, tzinfo=timezone.utc)
    except ValueError:
        return datetime(int(y), 1, 1, tzinfo=timezone.utc)


class PubmedAdapter(SourceAdapter):
    kind = "pubmed"

    def _common_params(self) -> dict:
        p = {"tool": "push-tool"}
        email = getattr(self.settings, "pubmed_email", "") if self.settings else ""
        if email:
            p["email"] = email
        return p

    async def fetch(self) -> list[RawItem]:
        term = self.config.get("term")
        if not term:
            raise ValueError(f"pubmed source {self.name!r} 缺少 config.term")
        retmax = int(self.config.get("max_results", 60))

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            search = await client.get(
                f"{EUTILS}/esearch.fcgi",
                params={
                    **self._common_params(),
                    "db": "pubmed",
                    "term": term,
                    "retmax": retmax,
                    "retmode": "json",
                    "sort": "date",
                },
            )
            search.raise_for_status()
            pmids = search.json().get("esearchresult", {}).get("idlist", [])
            if not pmids:
                return []

            await asyncio.sleep(0.4)  # 礼貌限速
            fetch = await client.get(
                f"{EUTILS}/efetch.fcgi",
                params={
                    **self._common_params(),
                    "db": "pubmed",
                    "id": ",".join(pmids),
                    "retmode": "xml",
                },
            )
            fetch.raise_for_status()
            xml = fetch.text

        root = await asyncio.to_thread(ET.fromstring, xml)
        items: list[RawItem] = []
        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//PMID")
            title = art.findtext(".//ArticleTitle") or ""
            abstract_parts = [
                (n.text or "") for n in art.findall(".//Abstract/AbstractText")
            ]
            abstract = " ".join(p for p in abstract_parts if p).strip() or None
            items.append(
                RawItem(
                    external_id=f"pmid:{pmid}" if pmid else None,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                    title=title.strip(),
                    abstract=abstract,
                    published_at=_pub_datetime(art),
                    raw_json={"source_name": self.name, "pmid": pmid},
                )
            )
        return items
