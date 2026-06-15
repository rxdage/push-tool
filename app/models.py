"""ORM 模型（第 4 节）。全部按 user_id 隔离，第一天就多租户。

主键用自增 Integer；多租户隔离靠 user_id 外键 + 查询过滤，而非主键类型。
"""
from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base

# ---- 枚举值（用字符串约束，避免迁移期 enum 维护成本）----
FEED_TYPES = ("industry", "academic")
SOURCE_KINDS = ("rss", "arxiv", "pubmed", "s2", "html", "classic")
BUCKETS = ("deep", "brief", "classic")
ACTION_LABELS = ("action", "watch")  # 或 NULL
CHANNEL_KINDS = ("feishu_bot", "wecom_bot", "service_account", "email")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    tz: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    profiles: Mapped[list["InterestProfile"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class InterestProfile(Base):
    """"精华"筛选的大脑配置——是数据，不写死在代码里。"""

    __tablename__ = "interest_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    must_have: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    exclude: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    notes: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="profiles")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="interest_profile"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    feed_type: Mapped[str] = mapped_column(String(20))  # industry|academic
    interest_profile_id: Mapped[int] = mapped_column(
        ForeignKey("interest_profiles.id", ondelete="RESTRICT")
    )
    schedule_cron: Mapped[str] = mapped_column(String(120))
    tz: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    max_deep: Mapped[int] = mapped_column(Integer, default=3)
    max_brief: Mapped[int] = mapped_column(Integer, default=6)
    max_classic: Mapped[int] = mapped_column(Integer, default=0)
    delivery_channel_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), default=list
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="subscriptions")
    interest_profile: Mapped[InterestProfile] = relationship(
        back_populates="subscriptions"
    )
    sources: Mapped[list["Source"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )
    digests: Mapped[list["Digest"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # rss|arxiv|pubmed|s2|html
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    subscription: Mapped[Subscription] = relationship(back_populates="sources")
    items: Mapped[list["Item"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dim), nullable=True
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    raw_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    source: Mapped[Source] = relationship(back_populates="items")
    digest_items: Mapped[list["DigestItem"]] = relationship(
        back_populates="item"
    )


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")

    subscription: Mapped[Subscription] = relationship(back_populates="digests")
    items: Mapped[list["DigestItem"]] = relationship(
        back_populates="digest", cascade="all, delete-orphan"
    )
    delivery_logs: Mapped[list["DeliveryLog"]] = relationship(
        back_populates="digest", cascade="all, delete-orphan"
    )


class DigestItem(Base):
    __tablename__ = "digest_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    digest_id: Mapped[int] = mapped_column(
        ForeignKey("digests.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), index=True
    )
    bucket: Mapped[str] = mapped_column(String(20))  # deep|brief|classic
    rank: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    classification: Mapped[str | None] = mapped_column(String(20), nullable=True)
    action_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)

    digest: Mapped[Digest] = relationship(back_populates="items")
    item: Mapped[Item] = relationship(back_populates="digest_items")


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    digest_id: Mapped[int] = mapped_column(
        ForeignKey("digests.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    response: Mapped[dict] = mapped_column(JSONB, default=dict)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    digest: Mapped[Digest] = relationship(back_populates="delivery_logs")


class Channel(Base):
    """Phase-2：渠道配置入库。MVP 阶段渠道可先走 env。"""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
