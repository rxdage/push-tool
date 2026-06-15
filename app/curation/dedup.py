"""去重：url/title 精确 + 嵌入余弦阈值。

pipeline 内对候选集去重；也可用 pgvector 对库内历史去重（find_similar_in_db）。
嵌入均已归一化，余弦 = 内积。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w一-鿿]+")


def normalize_title(title: str) -> str:
    t = (title or "").lower().strip()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.split("#", 1)[0].rstrip("/")
    return u.lower()


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class DedupResult:
    kept: list[Item]
    dropped: list[tuple[int, int]]  # (dropped_item_id, kept_item_id)


def dedup_candidates(items: list[Item], cosine_threshold: float = 0.92) -> DedupResult:
    """先 url/title 精确去重，再嵌入余弦去重。保留先出现的（调用方应先排好序）。"""
    kept: list[Item] = []
    dropped: list[tuple[int, int]] = []
    seen_url: dict[str, int] = {}
    seen_title: dict[str, int] = {}

    for it in items:
        nurl = normalize_url(it.url)
        ntitle = normalize_title(it.title)

        if nurl and nurl in seen_url:
            dropped.append((it.id, seen_url[nurl]))
            continue
        if ntitle and ntitle in seen_title:
            dropped.append((it.id, seen_title[ntitle]))
            continue

        # 嵌入近邻：与已保留项比对
        dup_of = None
        if it.embedding is not None:
            for k in kept:
                if k.embedding is None:
                    continue
                if _cosine(it.embedding, k.embedding) >= cosine_threshold:
                    dup_of = k.id
                    break
        if dup_of is not None:
            dropped.append((it.id, dup_of))
            continue

        kept.append(it)
        if nurl:
            seen_url[nurl] = it.id
        if ntitle:
            seen_title[ntitle] = it.id

    return DedupResult(kept=kept, dropped=dropped)


async def find_similar_in_db(
    session: AsyncSession,
    embedding: list[float],
    exclude_item_id: int | None = None,
    cosine_threshold: float = 0.92,
    limit: int = 5,
) -> list[tuple[int, float]]:
    """pgvector 余弦近邻（<=> 是 cosine distance；similarity = 1 - distance）。"""
    if embedding is None:
        return []
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
    params = {"vec": vec_literal, "limit": limit}
    # 条件拼 exclude 子句：避免 ":exclude IS NULL" 让 asyncpg 无法推断参数类型
    exclude_clause = ""
    if exclude_item_id is not None:
        exclude_clause = "AND id <> :exclude"
        params["exclude"] = exclude_item_id
    sql = sql_text(
        f"""
        SELECT id, 1 - (embedding <=> CAST(:vec AS vector)) AS similarity
        FROM items
        WHERE embedding IS NOT NULL
          {exclude_clause}
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, params)).all()
    return [(rid, sim) for rid, sim in rows if sim >= cosine_threshold]
