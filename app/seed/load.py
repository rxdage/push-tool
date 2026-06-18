"""幂等 seed：建 1 个 user、两个 InterestProfile、两个 Subscription + 各自 sources。

用法：
    python -m app.seed.load
    # docker-compose 内：docker compose run --rm app python -m app.seed.load

重复运行安全：按 name 查存在则复用；订阅已有 sources 则跳过加源。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import InterestProfile, Source, Subscription, User

SEED_DIR = Path(__file__).parent

# 订阅级配置（第 5 节）。profile_name 对应 profiles.yaml 里的 name。
SUBSCRIPTIONS = [
    {
        "name": "行业日报",
        "feed_type": "industry",
        "profile_name": "超薄 SiN 膜 / 微纳芯片平台",
        "schedule_cron": "30 7 * * 1-5",  # 每工作日 07:30
        "max_deep": 3,
        "max_brief": 6,
        "max_classic": 0,
        "sources_file": "sources_industry.yaml",
    },
    {
        "name": "学术周报",
        "feed_type": "academic",
        "profile_name": "固态纳米孔",
        "schedule_cron": "0 20 * * 0",  # 每周日 20:00
        "max_deep": 5,
        "max_brief": 15,
        "max_classic": 2,
        "sources_file": "sources_academic.yaml",
    },
    {
        "name": "行业周报",
        "feed_type": "industry",
        "profile_name": "超薄 SiN 膜 / 微纳芯片平台",  # 复用行业日报的画像与源
        "schedule_cron": "5 20 * * 0",  # 每周日 20:05（错开学术周报）→ 个人群
        "max_deep": 5,
        "max_brief": 12,
        "max_classic": 0,
        "sources_file": "sources_industry.yaml",
    },
]


def _load_yaml(name: str):
    with open(SEED_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


async def _get_or_create_user(session: AsyncSession) -> User:
    res = await session.execute(select(User).where(User.name == "default"))
    user = res.scalar_one_or_none()
    if user is None:
        user = User(name="default", tz=settings.tz_default)
        session.add(user)
        await session.flush()
        print(f"  + user default (id={user.id})")
    else:
        print(f"  = user default exists (id={user.id})")
    return user


async def _seed_profiles(session: AsyncSession, user: User) -> dict[str, InterestProfile]:
    by_name: dict[str, InterestProfile] = {}
    for row in _load_yaml("profiles.yaml"):
        res = await session.execute(
            select(InterestProfile).where(
                InterestProfile.user_id == user.id,
                InterestProfile.name == row["name"],
            )
        )
        prof = res.scalar_one_or_none()
        if prof is None:
            prof = InterestProfile(
                user_id=user.id,
                name=row["name"],
                description=(row.get("description") or "").strip(),
                keywords=row.get("keywords") or [],
                must_have=row.get("must_have") or [],
                exclude=row.get("exclude") or [],
                notes=(row.get("notes") or "").strip(),
            )
            session.add(prof)
            await session.flush()
            print(f"  + profile {prof.name!r} (id={prof.id})")
        else:
            print(f"  = profile {prof.name!r} exists (id={prof.id})")
        by_name[row["name"]] = prof
    return by_name


async def _seed_subscriptions(
    session: AsyncSession, user: User, profiles: dict[str, InterestProfile]
) -> None:
    for cfg in SUBSCRIPTIONS:
        profile = profiles[cfg["profile_name"]]
        res = await session.execute(
            select(Subscription).where(
                Subscription.user_id == user.id, Subscription.name == cfg["name"]
            )
        )
        sub = res.scalar_one_or_none()
        if sub is None:
            sub = Subscription(
                user_id=user.id,
                name=cfg["name"],
                feed_type=cfg["feed_type"],
                interest_profile_id=profile.id,
                schedule_cron=cfg["schedule_cron"],
                tz=settings.tz_default,
                max_deep=cfg["max_deep"],
                max_brief=cfg["max_brief"],
                max_classic=cfg["max_classic"],
                delivery_channel_ids=[],
                active=True,
            )
            session.add(sub)
            await session.flush()
            print(f"  + subscription {sub.name!r} (id={sub.id})")
        else:
            print(f"  = subscription {sub.name!r} exists (id={sub.id})")

        # 已有 sources 则跳过
        existing = await session.execute(
            select(Source.id).where(Source.subscription_id == sub.id)
        )
        if existing.first() is not None:
            print(f"    = sources already present for {sub.name!r}, skip")
            continue

        for src in _load_yaml(cfg["sources_file"]):
            session.add(
                Source(
                    subscription_id=sub.id,
                    kind=src["kind"],
                    config_json=src.get("config") or {},
                    weight=float(src.get("weight", 1.0)),
                    active=bool(src.get("active", True)),
                )
            )
        await session.flush()
        n = len(_load_yaml(cfg["sources_file"]))
        print(f"    + {n} sources for {sub.name!r}")


async def main() -> None:
    print("Seeding push-tool ...")
    async with SessionLocal() as session:
        user = await _get_or_create_user(session)
        profiles = await _seed_profiles(session, user)
        await _seed_subscriptions(session, user, profiles)
        await session.commit()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
