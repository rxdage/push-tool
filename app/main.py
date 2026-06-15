"""FastAPI app。Phase 1 只放健康检查；dashboard 路由 Phase 6 补。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.dashboard import router as dashboard_router
from app.db import engine, ensure_vector_extension


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 兜底确保 pgvector 扩展（迁移里已建）
    await ensure_vector_extension()
    yield
    await engine.dispose()


app = FastAPI(title="push-tool", lifespan=lifespan)
app.include_router(dashboard_router)


@app.get("/health")
async def health() -> dict:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/digests")
