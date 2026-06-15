"""本地多语嵌入（sentence-transformers，bge-m3，1024 维）。

懒加载模型（首次调用才下载/载入），避免无 GPU/无模型时阻塞 import。
去重/语义检索用，不走外部 API。
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

from app.config import settings

_MODEL = None


@lru_cache(maxsize=1)
def _load_model():
    """懒加载，缓存单例。"""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model)


def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """同步批量编码，归一化（便于用内积当余弦）。"""
    if not texts:
        return []
    model = _load_model()
    vecs = model.encode(
        texts, normalize_embeddings=True, convert_to_numpy=True
    )
    return [v.tolist() for v in vecs]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """异步包装：编码丢线程池，避免阻塞事件循环。"""
    if not texts:
        return []
    return await asyncio.to_thread(embed_texts_sync, texts)


def item_text(title: str, abstract: str | None) -> str:
    """拼 item 的可嵌入文本。"""
    parts = [title or ""]
    if abstract:
        parts.append(abstract)
    return "\n".join(parts).strip()[:4000]
