"""Pydantic schemas（读模型 / API 输出）。写侧 Phase 6 dashboard 再补。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InterestProfileOut(ORMModel):
    id: int
    user_id: int
    name: str
    description: str
    keywords: list[str]
    must_have: list[str]
    exclude: list[str]
    notes: str


class SourceOut(ORMModel):
    id: int
    subscription_id: int
    kind: str
    config_json: dict
    weight: float
    active: bool


class SubscriptionOut(ORMModel):
    id: int
    user_id: int
    name: str
    feed_type: str
    interest_profile_id: int
    schedule_cron: str
    tz: str
    max_deep: int
    max_brief: int
    max_classic: int
    delivery_channel_ids: list[int]
    active: bool


class ItemOut(ORMModel):
    id: int
    source_id: int
    external_id: str | None
    url: str | None
    title: str
    abstract: str | None
    published_at: datetime | None
    fetched_at: datetime
    tags: list[str]


class DigestItemOut(ORMModel):
    id: int
    item_id: int
    bucket: str
    rank: int
    summary: str
    classification: str | None
    action_label: str | None
    why_it_matters: str | None
    item: ItemOut | None = None


class DigestOut(ORMModel):
    id: int
    subscription_id: int
    run_at: datetime
    period_start: datetime | None
    period_end: datetime | None
    status: str
    items: list[DigestItemOut] = []
