"""source.kind -> adapter 映射。"""
from __future__ import annotations

from app.ingestion.arxiv import ArxivAdapter
from app.ingestion.base import SourceAdapter
from app.ingestion.html_scrape import HtmlScrapeAdapter
from app.ingestion.pubmed import PubmedAdapter
from app.ingestion.rss import RssAdapter
from app.ingestion.semantic_scholar import SemanticScholarAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    RssAdapter.kind: RssAdapter,
    ArxivAdapter.kind: ArxivAdapter,
    PubmedAdapter.kind: PubmedAdapter,
    SemanticScholarAdapter.kind: SemanticScholarAdapter,
    HtmlScrapeAdapter.kind: HtmlScrapeAdapter,
}


def build_adapter(kind: str, config: dict, *, settings=None) -> SourceAdapter:
    try:
        cls = ADAPTERS[kind]
    except KeyError as e:
        raise ValueError(f"未知 source.kind: {kind!r}") from e
    return cls(config, settings=settings)
