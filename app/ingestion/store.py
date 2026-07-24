"""把 RawItem 落库为 Item，按 (source_id, url) / (source_id, external_id) 去重。

DB 层已有唯一索引兜底；这里先查后插减少异常，逐条插入并吞掉竞态冲突。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.base import RawItem
from app.models import Item, Source


@dataclass
class StoreResult:
    source_id: int
    fetched: int
    inserted: int
    skipped: int

    def __str__(self) -> str:
        return (
            f"source#{self.source_id}: fetched={self.fetched} "
            f"inserted={self.inserted} skipped={self.skipped}"
        )


async def store_items(
    session: AsyncSession, source: Source, raws: list[RawItem]
) -> StoreResult:
    # 该源已存在的 url / external_id
    res = await session.execute(
        select(Item.url, Item.external_id).where(Item.source_id == source.id)
    )
    seen_urls: set[str] = set()
    seen_ext: set[str] = set()
    for url, ext in res.all():
        if url:
            seen_urls.add(url)
        if ext:
            seen_ext.add(ext)

    inserted = 0
    skipped = 0
    for r in raws:
        if (r.url and r.url in seen_urls) or (
            r.external_id and r.external_id in seen_ext
        ):
            skipped += 1
            continue
        item = Item(
            source_id=source.id,
            external_id=r.external_id,
            url=r.url,
            title=r.title or "",
            abstract=r.abstract,
            raw_text=r.raw_text,
            published_at=r.published_at,
            tags=r.tags or [],
            raw_json=r.raw_json or {},
        )
        # SAVEPOINT：单行出问题（唯一冲突 / 字段超长等数据类错误）只回滚这一行，
        # 不拖累本批已插入的条目——之前只兜 IntegrityError，某源一条脏数据（如
        # Google News 的超长 guid）曾经把整批（含其它正常源）一起回滚掉。
        try:
            async with session.begin_nested():
                session.add(item)
                await session.flush()
        except (IntegrityError, DataError) as e:
            print(f"    ! source#{source.id} 单条入库失败（跳过）: {e.__class__.__name__}")
            skipped += 1
            continue
        if r.url:
            seen_urls.add(r.url)
        if r.external_id:
            seen_ext.add(r.external_id)
        inserted += 1

    return StoreResult(
        source_id=source.id, fetched=len(raws), inserted=inserted, skipped=skipped
    )
