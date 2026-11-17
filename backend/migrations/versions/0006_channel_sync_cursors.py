"""Add durable external-channel synchronization cursors.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if inspector.has_table("channel_sync_cursors"):
        return
    op.create_table(
        "channel_sync_cursors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("cursor", sa.String(length=256), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "scope", name="uq_channel_sync_cursor"),
    )
    op.create_index(
        "ix_channel_sync_cursors_channel",
        "channel_sync_cursors",
        ["channel"],
    )


def downgrade() -> None:
    op.drop_index("ix_channel_sync_cursors_channel", table_name="channel_sync_cursors")
    op.drop_table("channel_sync_cursors")
