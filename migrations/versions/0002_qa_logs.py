"""qa_logs（飞书问答 bot 用量）

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("chat_id", sa.String(120), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("question", sa.Text, nullable=False, server_default=""),
        sa.Column("answered", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_qa_logs_chat_id", "qa_logs", ["chat_id"])
    op.create_index("ix_qa_logs_user_id", "qa_logs", ["user_id"])
    op.create_index("ix_qa_logs_created_at", "qa_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("qa_logs")
