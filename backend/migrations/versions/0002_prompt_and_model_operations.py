"""Add versioned system prompts and administrative operation audit.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_releases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_releases_checksum", "prompt_releases", ["checksum"])
    op.create_index("ix_prompt_releases_status", "prompt_releases", ["status"])
    op.create_table(
        "admin_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("error", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_operations_kind", "admin_operations", ["kind"])
    op.create_index("ix_admin_operations_status", "admin_operations", ["status"])
    op.create_index("ix_admin_operations_target_id", "admin_operations", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_operations_target_id", table_name="admin_operations")
    op.drop_index("ix_admin_operations_status", table_name="admin_operations")
    op.drop_index("ix_admin_operations_kind", table_name="admin_operations")
    op.drop_table("admin_operations")
    op.drop_index("ix_prompt_releases_status", table_name="prompt_releases")
    op.drop_index("ix_prompt_releases_checksum", table_name="prompt_releases")
    op.drop_table("prompt_releases")
