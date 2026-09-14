"""只读诊断：用指定画像对候选跑一遍 LLM 精筛，看能选出几条。

不写库、不建 digest，只打印结果。用来对比宽/窄口径的命中差异，或预演日更效果。

    # 复用某期 digest 的时间窗
    docker compose run --rm app python scripts/diag_summarize.py <sub_id> <digest_id> <profile_id>
    # digest_id 传 0 = 用「现在往前推该订阅的回看天数」，预演下一期
    docker compose run --rm app python scripts/diag_summarize.py 2 0 2
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.curation.dedup import dedup_candidates, find_similar_in_db  # noqa: E402
from app.curation.pipeline import (  # noqa: E402
    RELEVANCE_GATE,
    _delivered_item_ids,
    _ensure_embeddings,
    _load_candidates,
    _lookback_days,
    is_academic_source_item,
    is_low_value_item,
)
from app.curation.score import score_items  # noqa: E402
from app.curation.summarize import DEFAULT_MODEL, summarize_items  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Digest, InterestProfile, Subscription  # noqa: E402


async def main(sub_id: int, digest_id: int, profile_id: int) -> None:
    async with SessionLocal() as s:
        sub = await s.get(Subscription, sub_id)
        prof = await s.get(InterestProfile, profile_id)
        if digest_id:
            period_start = (await s.get(Digest, digest_id)).period_start
        else:
            period_start = datetime.now(timezone.utc) - timedelta(days=_lookback_days(sub))
        print(f"订阅 {sub.name!r} / 用画像 {prof.name!r} / 窗口起点 {period_start}")

        cands = await _load_candidates(s, sub, period_start)
        if sub.feed_type == "industry":
            cands = [i for i in cands if not is_academic_source_item(i)]
            cands = [i for i in cands if not is_low_value_item(i)]
        # 必须和 run_pipeline 同序：先补嵌入。少了这步 score_items 的 have_emb=all(...)
        # 判定为假，会整体退回关键词重叠，长短语关键词字面匹配不上 → 全 0 分、排序随机。
        await _ensure_embeddings(s, cands)
        await s.commit()
        kept = dedup_candidates(cands).kept
        scored = await score_items(kept, prof)
        delivered = await _delivered_item_ids(s, sub)
        # 与 run_pipeline 同序：先按 id 剔除已发过的，再截断 top-N
        fresh = [x.item for x in scored if x.item.id not in delivered]
        budget = sub.max_deep + sub.max_brief + sub.max_classic
        shortlist = fresh[: max(10, budget * 3)]
        print(f"候选 {len(cands)} → 组内去重 {len(kept)} → 剔已发过 {len(fresh)} → top-N {len(shortlist)}")

        survivors = []
        for it in shortlist:
            if it.embedding is not None:
                sims = await find_similar_in_db(
                    s, it.embedding, exclude_item_id=it.id, cosine_threshold=0.92
                )
                if {sid for sid, _ in sims} & delivered:
                    continue
            survivors.append(it)
        print(f"进 LLM 精筛: {len(survivors)} 条，闸门 relevance >= {RELEVANCE_GATE}\n")

        res = await summarize_items(survivors, prof, sub.feed_type, DEFAULT_MODEL)
        rows = [(it, res[it.id]) for it in survivors if it.id in res]
        rows.sort(key=lambda r: r[1]["relevance"], reverse=True)
        n_pass = 0
        for it, r in rows:
            mark = "✅" if r["relevance"] >= RELEVANCE_GATE else "  "
            if r["relevance"] >= RELEVANCE_GATE:
                n_pass += 1
            print(f"{mark} {r['relevance']:.2f} item#{it.id} {(it.title or '')[:62]}")
            if r["relevance"] >= RELEVANCE_GATE:
                print(f"      {r['summary']}")
        print(f"\n过闸门: {n_pass} / {len(rows)}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])))
