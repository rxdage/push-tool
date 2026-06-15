"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from pgvector.sqlalchemy import Vector

from app.config import settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DIM = settings.embedding_dim


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tz", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    op.create_table(
        "interest_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("keywords", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("must_have", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("exclude", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_interest_profiles_user_id", "interest_profiles", ["user_id"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("feed_type", sa.String(20), nullable=False),
        sa.Column(
            "interest_profile_id",
            sa.Integer,
            sa.ForeignKey("interest_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("schedule_cron", sa.String(120), nullable=False),
        sa.Column("tz", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("max_deep", sa.Integer, nullable=False, server_default="3"),
        sa.Column("max_brief", sa.Integer, nullable=False, server_default="6"),
        sa.Column("max_classic", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "delivery_channel_ids",
            ARRAY(sa.Integer),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer,
            sa.ForeignKey("subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("config_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("weight", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_sources_subscription_id", "sources", ["subscription_id"])

    op.create_table(
        "items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("abstract", sa.Text, nullable=True),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("embedding", Vector(DIM), nullable=True),
        sa.Column("tags", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("raw_json", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_items_source_id", "items", ["source_id"])
    # 去重：同源下 url / external_id 唯一（NULL 不冲突）
    op.create_index(
        "uq_items_source_url", "items", ["source_id", "url"], unique=True
    )
    op.create_index(
        "uq_items_source_external_id",
        "items",
        ["source_id", "external_id"],
        unique=True,
    )
    # pgvector 余弦近邻索引（Phase 7 语义去重/检索用）
    op.create_index(
        "ix_items_embedding_hnsw",
        "items",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "digests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer,
            sa.ForeignKey("subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.create_index("ix_digests_subscription_id", "digests", ["subscription_id"])

    op.create_table(
        "digest_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "digest_id",
            sa.Integer,
            sa.ForeignKey("digests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.Integer,
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bucket", sa.String(20), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("classification", sa.String(20), nullable=True),
        sa.Column("action_label", sa.String(20), nullable=True),
        sa.Column("why_it_matters", sa.Text, nullable=True),
    )
    op.create_index("ix_digest_items_digest_id", "digest_items", ["digest_id"])
    op.create_index("ix_digest_items_item_id", "digest_items", ["item_id"])

    op.create_table(
        "delivery_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "digest_id",
            sa.Integer,
            sa.ForeignKey("digests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("response", JSONB, nullable=False, server_default="{}"),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_delivery_logs_digest_id", "delivery_logs", ["digest_id"])

    op.create_table(
        "channels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("config_json", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_channels_user_id", "channels", ["user_id"])


def downgrade() -> None:
    op.drop_table("channels")
    op.drop_table("delivery_logs")
    op.drop_table("digest_items")
    op.drop_table("digests")
    op.drop_index("ix_items_embedding_hnsw", table_name="items")
    op.drop_table("items")
    op.drop_table("sources")
    op.drop_table("subscriptions")
    op.drop_table("interest_profiles")
    op.drop_table("users")
