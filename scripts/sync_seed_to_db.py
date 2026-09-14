"""把 seed yaml 的改动同步到已有库（seed.load 只建不改，改画像/加源要用这个）。

做三件事，都幂等：
  1. 画像：按 name 匹配，用 profiles.yaml 覆盖 description/keywords/must_have/exclude/notes
  2. 加源：sources_*.yaml 里有、库里没有的（按 config.name 匹配），补进对应订阅
  3. 报告：库里有、yaml 里没有的"孤儿源"只打印不动（要停用得显式传 --deactivate-orphans）

    docker compose run --rm app python scripts/sync_seed_to_db.py --dry-run
    docker compose run --rm app python scripts/sync_seed_to_db.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import InterestProfile, Source, Subscription  # noqa: E402
from app.seed.load import SUBSCRIPTIONS, _load_yaml  # noqa: E402


def _src_name(config: dict) -> str:
    return (config or {}).get("name") or ""


async def sync_profiles(session, dry: bool) -> None:
    print("== 画像 ==")
    for row in _load_yaml("profiles.yaml"):
        prof = (
            await session.execute(
                select(InterestProfile).where(InterestProfile.name == row["name"])
            )
        ).scalars().first()
        if prof is None:
            print(f"  ! 库里没有画像 {row['name']!r}（跑 seed.load 建）")
            continue
        new = {
            "description": (row.get("description") or "").strip(),
            "keywords": row.get("keywords") or [],
            "must_have": row.get("must_have") or [],
            "exclude": row.get("exclude") or [],
            "notes": (row.get("notes") or "").strip(),
        }
        changed = [k for k, v in new.items() if getattr(prof, k) != v]
        if not changed:
            print(f"  = {prof.name!r} 无变化")
            continue
        print(f"  ~ {prof.name!r} 更新 {changed}")
        if not dry:
            for k, v in new.items():
                setattr(prof, k, v)


async def sync_sources(session, dry: bool, deactivate_orphans: bool) -> None:
    print("\n== 源 ==")
    for cfg in SUBSCRIPTIONS:
        sub = (
            await session.execute(
                select(Subscription).where(Subscription.name == cfg["name"])
            )
        ).scalars().first()
        if sub is None:
            print(f"  ! 库里没有订阅 {cfg['name']!r}，跳过")
            continue

        yaml_rows = _load_yaml(cfg["sources_file"])
        yaml_names = {_src_name(r.get("config")) for r in yaml_rows}

        existing = (
            await session.execute(select(Source).where(Source.subscription_id == sub.id))
        ).scalars().all()
        have = {_src_name(s.config_json): s for s in existing}

        added = 0
        for r in yaml_rows:
            n = _src_name(r.get("config"))
            if n in have:
                continue
            print(f"  + [{sub.name}] {n!r} ({r['kind']})")
            added += 1
            if not dry:
                session.add(
                    Source(
                        subscription_id=sub.id,
                        kind=r["kind"],
                        config_json=r.get("config") or {},
                        weight=float(r.get("weight", 1.0)),
                        active=bool(r.get("active", True)),
                    )
                )

        orphans = [s for n, s in have.items() if n and n not in yaml_names and s.active]
        for s in orphans:
            tag = "停用" if deactivate_orphans else "仅报告"
            print(f"  - [{sub.name}] 孤儿源 {_src_name(s.config_json)!r} (id={s.id}) → {tag}")
            if deactivate_orphans and not dry:
                s.active = False
        if not added and not orphans:
            print(f"  = [{sub.name}] 源已同步（{len(existing)} 个）")


async def main(dry: bool, deactivate_orphans: bool) -> None:
    async with SessionLocal() as session:
        await sync_profiles(session, dry)
        await sync_sources(session, dry, deactivate_orphans)
        if dry:
            print("\n[dry-run] 未写库")
        else:
            await session.commit()
            print("\n已提交。")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--deactivate-orphans",
        action="store_true",
        help="把库里有、yaml 里已删除的源置为 active=false",
    )
    a = p.parse_args()
    asyncio.run(main(a.dry_run, a.deactivate_orphans))
