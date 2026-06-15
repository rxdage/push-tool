"""极简 dashboard（FastAPI + Jinja/HTMX）。

路由：/digests、/digest/{id}、/subscriptions、/sources、/items（检索）。
全部按 user_id 过滤（现单用户，auth 打桩为 default user）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.delivery.formatter import build_view, to_dashboard_payload
from app.models import (
    Digest,
    DigestItem,
    Item,
    Source,
    Subscription,
    User,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()


async def current_user(session: AsyncSession) -> User:
    """auth 打桩：返回第一个 user。Phase-2 换成真实鉴权。"""
    user = (await session.execute(select(User).order_by(User.id))).scalars().first()
    if user is None:
        raise HTTPException(503, "尚未 seed user，先跑 python -m app.seed.load")
    return user


@router.get("/digests", response_class=HTMLResponse)
async def list_digests(request: Request, session: AsyncSession = Depends(get_session)):
    user = await current_user(session)
    rows = (
        await session.execute(
            select(Digest, Subscription, func.count(DigestItem.id))
            .join(Subscription, Digest.subscription_id == Subscription.id)
            .outerjoin(DigestItem, DigestItem.digest_id == Digest.id)
            .where(Subscription.user_id == user.id)
            .group_by(Digest.id, Subscription.id)
            .order_by(Digest.run_at.desc())
            .limit(100)
        )
    ).all()
    digests = [
        {"digest": d, "subscription": s, "count": c} for d, s, c in rows
    ]
    return templates.TemplateResponse(
        "digests.html", {"request": request, "digests": digests}
    )


@router.get("/digest/{digest_id}", response_class=HTMLResponse)
async def read_digest(
    digest_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    user = await current_user(session)
    digest = await session.get(Digest, digest_id)
    if digest is None:
        raise HTTPException(404, "digest 不存在")
    sub = await session.get(Subscription, digest.subscription_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(403, "无权访问")
    view = await build_view(session, digest)
    return templates.TemplateResponse(
        "digest.html",
        {"request": request, "digest": digest, "payload": to_dashboard_payload(view)},
    )


@router.get("/subscriptions", response_class=HTMLResponse)
async def list_subscriptions(
    request: Request, session: AsyncSession = Depends(get_session)
):
    user = await current_user(session)
    subs = (
        await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.id)
        )
    ).scalars().all()
    counts = dict(
        (
            await session.execute(
                select(Source.subscription_id, func.count(Source.id))
                .where(Source.subscription_id.in_([s.id for s in subs] or [0]))
                .group_by(Source.subscription_id)
            )
        ).all()
    )
    return templates.TemplateResponse(
        "subscriptions.html",
        {"request": request, "subscriptions": subs, "source_counts": counts},
    )


@router.post("/subscriptions/{sub_id}/toggle", response_class=HTMLResponse)
async def toggle_subscription(
    sub_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    user = await current_user(session)
    sub = await session.get(Subscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(404, "订阅不存在")
    sub.active = not sub.active
    await session.commit()
    return templates.TemplateResponse(
        "_toggle.html",
        {"request": request, "active": sub.active, "kind": "subscriptions", "id": sub.id},
    )


@router.get("/sources", response_class=HTMLResponse)
async def list_sources(request: Request, session: AsyncSession = Depends(get_session)):
    user = await current_user(session)
    rows = (
        await session.execute(
            select(Source, Subscription.name)
            .join(Subscription, Source.subscription_id == Subscription.id)
            .where(Subscription.user_id == user.id)
            .order_by(Source.subscription_id, Source.id)
        )
    ).all()
    sources = [{"source": s, "sub_name": n} for s, n in rows]
    return templates.TemplateResponse(
        "sources.html", {"request": request, "sources": sources}
    )


@router.post("/sources/{source_id}/toggle", response_class=HTMLResponse)
async def toggle_source(
    source_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    user = await current_user(session)
    src = (
        await session.execute(
            select(Source)
            .join(Subscription, Source.subscription_id == Subscription.id)
            .where(Source.id == source_id, Subscription.user_id == user.id)
        )
    ).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "源不存在")
    src.active = not src.active
    await session.commit()
    return templates.TemplateResponse(
        "_toggle.html",
        {"request": request, "active": src.active, "kind": "sources", "id": src.id},
    )


async def _semantic_search(
    session: AsyncSession, user_id: int, query: str, limit: int = 50
) -> list[Item]:
    """pgvector 余弦语义检索，按 user_id 过滤。"""
    from sqlalchemy import text as sql_text

    from app.curation.embed import embed_texts

    vec = (await embed_texts([query]))[0]
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    sql = sql_text(
        """
        SELECT i.* FROM items i
        JOIN sources s ON i.source_id = s.id
        JOIN subscriptions sub ON s.subscription_id = sub.id
        WHERE sub.user_id = :uid AND i.embedding IS NOT NULL
        ORDER BY i.embedding <=> CAST(:vec AS vector)
        LIMIT :limit
        """
    )
    rows = await session.execute(
        sql.columns(*Item.__table__.columns),
        {"uid": user_id, "vec": vec_literal, "limit": limit},
    )
    return list(rows.scalars().all())


@router.get("/items", response_class=HTMLResponse)
async def search_items(
    request: Request,
    q: str = "",
    mode: str = "text",
    session: AsyncSession = Depends(get_session),
):
    """检索：mode=text 标题模糊；mode=semantic pgvector 语义近邻。"""
    user = await current_user(session)
    error = None

    if mode == "semantic" and q.strip():
        try:
            items = await _semantic_search(session, user.id, q.strip())
        except Exception as e:  # noqa: BLE001 — 嵌入模型不可用时退回文本检索
            error = f"语义检索不可用，已退回标题检索：{e}"
            mode = "text"
            items = None
    else:
        items = None

    if items is None:
        stmt = (
            select(Item)
            .join(Source, Item.source_id == Source.id)
            .join(Subscription, Source.subscription_id == Subscription.id)
            .where(Subscription.user_id == user.id)
        )
        if q.strip():
            stmt = stmt.where(Item.title.ilike(f"%{q.strip()}%"))
        stmt = stmt.order_by(Item.fetched_at.desc()).limit(50)
        items = (await session.execute(stmt)).scalars().all()

    return templates.TemplateResponse(
        "items.html",
        {"request": request, "items": items, "q": q, "mode": mode, "error": error},
    )
