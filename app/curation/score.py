"""相关性初筛：嵌入相似度（profile 向量 vs item 向量）。

LLM 闸门在 summarize 阶段。这里做廉价的嵌入相似度初筛，先砍掉明显不相关的，
再把 top-N 交给 Claude 精筛，省 token。无嵌入时退回关键词重叠。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.curation.embed import embed_texts
from app.models import InterestProfile, Item


def profile_query_text(profile: InterestProfile) -> str:
    parts = [profile.description or "", " ".join(profile.keywords or [])]
    return "\n".join(p for p in parts if p).strip()


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _keyword_overlap(item: Item, profile: InterestProfile) -> float:
    """退路：item 文本里命中多少 keyword/must_have（归一到 0–1）。"""
    hay = f"{item.title} {item.abstract or ''}".lower()
    terms = [t.lower() for t in (profile.keywords or []) + (profile.must_have or [])]
    if not terms:
        return 0.0
    hits = sum(1 for t in terms if t and t in hay)
    return min(1.0, hits / max(3, len(terms) / 3))


@dataclass
class ScoredItem:
    item: Item
    score: float


async def score_items(
    items: list[Item], profile: InterestProfile
) -> list[ScoredItem]:
    """对每个 item 打 0–1 相关性初筛分，降序返回。"""
    if not items:
        return []

    have_emb = all(it.embedding is not None for it in items)
    scored: list[ScoredItem] = []

    if have_emb:
        try:
            pvec = (await embed_texts([profile_query_text(profile)]))[0]
            for it in items:
                scored.append(ScoredItem(it, _cosine(pvec, it.embedding)))
        except Exception as e:  # noqa: BLE001 — 嵌入模型不可用则退回关键词
            print(f"  ! 嵌入打分失败，退回关键词重叠: {e}")
            scored = []

    if not scored:
        scored = [ScoredItem(it, _keyword_overlap(it, profile)) for it in items]

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
