"""items.external_id: VARCHAR(512) -> TEXT（部分源如 Google News RSS 的 guid 超长）

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "items", "external_id", existing_type=sa.String(512), type_=sa.Text()
    )


def downgrade() -> None:
    op.alter_column(
        "items", "external_id", existing_type=sa.Text(), type_=sa.String(512)
    )
