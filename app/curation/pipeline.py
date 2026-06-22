"""筛选 pipeline：ingest 已入库后，跑 dedup → score → summarize/classify → rank → bucket。

产出一个 Digest + 若干 DigestItem。投递（format/deliver）在 Phase 4。
入口：run_pipeline(session, subscription)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.curation.classic import (
    CLASSIC_KIND,
    ensure_classic_items,
    select_classics,
)
from app.curation.dedup import dedup_candidates, find_similar_in_db
from app.curation.embed import embed_texts, item_text
from app.curation.score import score_items
from app.curation.summarize import (
    DEFAULT_MODEL,
    build_batch_requests,
    collect_batch_results,
    summarize_items,
)
from app.models import Digest, DigestItem, InterestProfile, Item, Source, Subscription

# 相关性闸门：LLM 判定 relevance 低于此值不进 digest。
RELEVANCE_GATE = 0.4
# 默认回看窗口（天），按 feed_type。
LOOKBACK_DAYS = {"industry": 1, "academic": 7}


@dataclass
class PipelineConfig:
    use_batch: bool = False
    model: str = DEFAULT_MODEL
    relevance_gate: float = RELEVANCE_GATE


async def _load_candidates(
    session: AsyncSession, subscription: Subscription, period_start: datetime
) -> list[Item]:
    source_ids = (
        await session.execute(
            select(Source.id).where(
                Source.subscription_id == subscription.id,
                Source.kind != CLASSIC_KIND,  # 经典种子单独走 classic 桶
            )
        )
    ).scalars().all()
    if not source_ids:
        return []
    rows = await session.execute(
        select(Item)
        .where(Item.source_id.in_(source_ids), Item.fetched_at >= period_start)
        .order_by(Item.published_at.desc().nullslast(), Item.fetched_at.desc())
    )
    return list(rows.scalars().all())


async def _ensure_embeddings(session: AsyncSession, items: list[Item]) -> None:
    """给缺嵌入的 item 补嵌入并持久化。失败则跳过（pipeline 退回关键词）。"""
    missing = [it for it in items if it.embedding is None]
    if not missing:
        return
    try:
        vecs = await embed_texts([item_text(it.title, it.abstract) for it in missing])
    except Exception as e:  # noqa: BLE001
        print(f"  ! 嵌入生成失败，跳过（后续退回关键词）: {e}")
        return
    for it, v in zip(missing, vecs):
        it.embedding = v
    await session.flush()


async def _summarize(
    items: list[Item],
    profile: InterestProfile,
    feed_type: str,
    cfg: PipelineConfig,
) -> dict[int, dict]:
    if cfg.use_batch:
        import anthropic

        from app.config import settings

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        requests = build_batch_requests(items, profile, feed_type, cfg.model)
        batch = client.messages.batches.create(requests=requests)
        # 批量是异步的；这里只提交，轮询/收集由调用方择机做。
        # MVP：阻塞轮询直到结束（量小、可接受）。
        import time

        while True:
            b = client.messages.batches.retrieve(batch.id)
            if b.processing_status == "ended":
                break
            time.sleep(30)
        return collect_batch_results(client, batch.id)
    return await summarize_items(items, profile, feed_type, cfg.model)


def _bucket_deep_brief(
    passed: list[tuple[Item, dict]], subscription: Subscription
) -> list[tuple[Item, dict, str, int]]:
    """deep/brief 分桶（classic 桶单独走 _add_classics）。"""
    deep = passed[: subscription.max_deep]
    brief = passed[subscription.max_deep : subscription.max_deep + subscription.max_brief]
    out: list[tuple[Item, dict, str, int]] = []
    for rank, (it, r) in enumerate(deep):
        out.append((it, r, "deep", rank))
    for rank, (it, r) in enumerate(brief):
        out.append((it, r, "brief", rank))
    return out


async def _delivered_item_ids(
    session: AsyncSession, subscription: Subscription
) -> tuple[set[int], set[int]]:
    """历史已投递过的 item_id，分两组（用于跨期 / 跨订阅语义去重）：

    - own：本订阅自己投递过的。
    - sibling：共享同一 interest_profile 的「兄弟」订阅投递过的（如 行业日报↔行业周报）。
      用来阻止同一篇文章在日报、周报里各发一次。
    """
    own_rows = await session.execute(
        select(DigestItem.item_id)
        .join(Digest, DigestItem.digest_id == Digest.id)
        .where(Digest.subscription_id == subscription.id)
    )
    own = {iid for (iid,) in own_rows.all()}

    sib_sub_ids = (
        await session.execute(
            select(Subscription.id).where(
                Subscription.interest_profile_id == subscription.interest_profile_id,
                Subscription.id != subscription.id,
            )
        )
    ).scalars().all()
    sibling: set[int] = set()
    if sib_sub_ids:
        sib_rows = await session.execute(
            select(DigestItem.item_id)
            .join(Digest, DigestItem.digest_id == Digest.id)
            .where(Digest.subscription_id.in_(sib_sub_ids))
        )
        sibling = {iid for (iid,) in sib_rows.all()}
    return own, sibling


async def _drop_delivered_duplicates(
    session: AsyncSession,
    shortlist: list[Item],
    own_ids: set[int],
    sibling_ids: set[int],
    high_value_ids: set[int],
    threshold: float = 0.92,
) -> list[Item]:
    """跨期 / 跨订阅语义去重（pgvector 余弦）：

    - 与「本订阅」历史近重复 → 总是剔除：保持每期新鲜，并把单篇上限自然压到 2 次。
    - 与「兄弟订阅」历史近重复（如日报已发）→ 仅当本篇为高价值（会进 deep 桶）才保留，
      允许评分高 / 权威重要的文章跨订阅再发一次（最多 2 次）；其余剔除。
    """
    if not own_ids and not sibling_ids:
        return shortlist
    kept: list[Item] = []
    for it in shortlist:
        if it.embedding is None:
            kept.append(it)
            continue
        sims = await find_similar_in_db(
            session, it.embedding, exclude_item_id=it.id, cosine_threshold=threshold
        )
        sim_ids = {sid for sid, _ in sims}
        if sim_ids & own_ids:
            continue  # 本订阅发过 → 不重复
        if (sim_ids & sibling_ids) and it.id not in high_value_ids:
            continue  # 兄弟订阅发过且非高价值 → 剔除
        kept.append(it)
    return kept


async def _add_classics(
    session: AsyncSession,
    digest: Digest,
    subscription: Subscription,
    fresh_candidates: list[Item],
    profile: InterestProfile,
    cfg: PipelineConfig,
    offset: int,
    used_ids: set[int],
) -> None:
    """经典回顾桶：人工种子轮换 + S2 高引用兜底，summarize 后入库。"""
    classic_items = await ensure_classic_items(session, subscription)
    high_cit = [
        it
        for it in fresh_candidates
        if it.id not in used_ids and "high-citation" in (it.tags or [])
    ]
    picked = select_classics(classic_items, high_cit, subscription.max_classic, offset)
    if not picked:
        return

    results = await summarize_items(picked, profile, subscription.feed_type, cfg.model)
    for rank, it in enumerate(picked):
        r = results.get(it.id)
        summary = r["summary"] if r else (it.abstract or it.title or "")[:120]
        action = r["action_label"] if r else None
        why = r["why_it_matters"] if r else None
        session.add(
            DigestItem(
                digest_id=digest.id,
                item_id=it.id,
                bucket="classic",
                rank=rank,
                summary=summary,
                classification="classic",
                action_label=action,
                why_it_matters=why,
            )
        )


async def run_pipeline(
    session: AsyncSession,
    subscription: Subscription,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    cfg: PipelineConfig | None = None,
) -> Digest:
    cfg = cfg or PipelineConfig()
    now = datetime.now(timezone.utc)
    period_end = period_end or now
    if period_start is None:
        days = LOOKBACK_DAYS.get(subscription.feed_type, 1)
        period_start = period_end - timedelta(days=days)

    profile = await session.get(InterestProfile, subscription.interest_profile_id)
    if profile is None:
        raise ValueError(f"subscription#{subscription.id} 无 interest_profile")

    # 经典轮换偏移 = 此前已生成的 digest 数（在创建本期 digest 前取）
    classic_offset = await count_digests(session, subscription.id)

    digest = Digest(
        subscription_id=subscription.id,
        run_at=now,
        period_start=period_start,
        period_end=period_end,
        status="running",
    )
    session.add(digest)
    await session.flush()

    candidates = await _load_candidates(session, subscription, period_start)
    if not candidates:
        digest.status = "empty"
        await session.flush()
        return digest

    await _ensure_embeddings(session, candidates)

    deduped = dedup_candidates(candidates).kept
    scored = await score_items(deduped, profile)

    # 取 top-N 交给 LLM 精筛（留 3x 余量）
    budget = (subscription.max_deep + subscription.max_brief + subscription.max_classic)
    top_n = max(10, budget * 3)
    shortlist = [s.item for s in scored[:top_n]]

    # 跨期 / 跨订阅语义去重：剔除已投递的近重复；高价值文章允许跨订阅再发一次（上限 2）
    # 高价值 = 按预筛分数排在前 max_deep（即会进 deep“深度精读”桶）的候选
    own_ids, sibling_ids = await _delivered_item_ids(session, subscription)
    high_value_ids = {it.id for it in shortlist[: subscription.max_deep]}
    shortlist = await _drop_delivered_duplicates(
        session, shortlist, own_ids, sibling_ids, high_value_ids
    )

    results = await _summarize(shortlist, profile, subscription.feed_type, cfg)

    # 闸门 + 关联 item，按 relevance 降序
    passed = [
        (it, results[it.id])
        for it in shortlist
        if it.id in results and results[it.id]["relevance"] >= cfg.relevance_gate
    ]
    passed.sort(key=lambda pr: pr[1]["relevance"], reverse=True)

    used_ids: set[int] = set()
    for it, r, bucket, rank in _bucket_deep_brief(passed, subscription):
        used_ids.add(it.id)
        session.add(
            DigestItem(
                digest_id=digest.id,
                item_id=it.id,
                bucket=bucket,
                rank=rank,
                summary=r["summary"],
                classification=r["classification"],
                action_label=r["action_label"],
                why_it_matters=r["why_it_matters"],
            )
        )

    # 经典回顾桶（轮换 + 高引用兜底）
    if subscription.max_classic > 0:
        await _add_classics(
            session, digest, subscription, candidates, profile, cfg,
            classic_offset, used_ids,
        )

    digest.status = "ready"
    await session.flush()
    return digest


async def count_digests(session: AsyncSession, subscription_id: int) -> int:
    return (
        await session.execute(
            select(func.count(Digest.id)).where(
                Digest.subscription_id == subscription_id
            )
        )
    ).scalar_one()
