"""只读诊断：复现某个订阅的筛选前半段，看条目在哪一步被砍光。

不创建 digest、不调 LLM（在 summarize 之前停）。

    docker compose run --rm app python scripts/diag_pipeline.py <sub_id> [digest_id]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.curation.dedup import dedup_candidates  # noqa: E402
from app.curation.pipeline import (  # noqa: E402
    _delivered_item_ids,
    _load_candidates,
    _lookback_days,
    is_academic_source_item,
    is_low_value_item,
)
from app.curation.score import score_items  # noqa: E402
from app.curation.dedup import find_similar_in_db  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Digest, InterestProfile, Subscription  # noqa: E402


async def main(sub_id: int, digest_id: int | None) -> None:
    async with SessionLocal() as s:
        sub = await s.get(Subscription, sub_id)
        prof = await s.get(InterestProfile, sub.interest_profile_id)
        print(f"订阅 {sub.name!r} 画像={prof.name!r} 回看={_lookback_days(sub)}天 "
              f"上限 deep={sub.max_deep} brief={sub.max_brief}")

        if digest_id:
            d = await s.get(Digest, digest_id)
            period_start = d.period_start
            print(f"复用 digest#{digest_id} 的窗口 period_start={period_start}")
        else:
            raise SystemExit("请传 digest_id 以复用同一时间窗")

        cands = await _load_candidates(s, sub, period_start)
        print(f"\n1) 窗口内候选: {len(cands)}")

        if sub.feed_type == "industry":
            n0 = len(cands)
            cands = [i for i in cands if not is_academic_source_item(i)]
            n1 = len(cands)
            cands = [i for i in cands if not is_low_value_item(i)]
            print(f"2) 行业过滤: 论文 -{n0 - n1}，低信息量 -{n1 - len(cands)} → 剩 {len(cands)}")

        kept = dedup_candidates(cands).kept
        print(f"3) 组内去重: → {len(kept)}")

        scored = await score_items(kept, prof)
        budget = sub.max_deep + sub.max_brief + sub.max_classic
        top_n = max(10, budget * 3)
        shortlist = [x.item for x in scored[:top_n]]
        print(f"4) 初筛打分 top_n={top_n} → shortlist {len(shortlist)}")
        for x in scored[:8]:
            print(f"     {x.score:.3f}  {(x.item.title or '')[:70]}")

        delivered = await _delivered_item_ids(s, sub)
        print(f"\n5) 历史去重集合: {len(delivered)} 条")
        by_self, by_neighbor, survived = [], [], []
        for it in shortlist:
            if it.id in delivered:
                by_self.append(it)
                continue
            if it.embedding is None:
                survived.append((it, None))
                continue
            sims = await find_similar_in_db(s, it.embedding, exclude_item_id=it.id,
                                            cosine_threshold=0.92)
            hit = [(sid, sim) for sid, sim in sims if sid in delivered]
            if hit:
                by_neighbor.append((it, hit[0]))
            else:
                survived.append((it, max(sims, key=lambda t: t[1], default=None)))
        print(f"   被剔除(本条发过): {len(by_self)}")
        for it in by_self[:10]:
            print(f"     - {(it.title or '')[:70]}")
        print(f"   被剔除(近重复): {len(by_neighbor)}")
        for it, (sid, sim) in by_neighbor[:10]:
            print(f"     - sim={sim:.3f} vs item#{sid}  {(it.title or '')[:60]}")
        print(f"   存活进 LLM 精筛: {len(survived)}")
        for it, near in survived[:15]:
            extra = f"  (最近邻 sim={near[1]:.3f})" if near else ""
            print(f"     + {(it.title or '')[:70]}{extra}")


if __name__ == "__main__":
    sub_id = int(sys.argv[1])
    digest_id = int(sys.argv[2]) if len(sys.argv) > 2 else None
    asyncio.run(main(sub_id, digest_id))
