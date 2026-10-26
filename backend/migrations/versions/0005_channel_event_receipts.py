"""Add durable external-channel callback deduplication.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if inspector.has_table("channel_event_receipts"):
        return
    op.create_table(
        "channel_event_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "event_id", name="uq_channel_event_receipt"),
    )
    op.create_index(
        "ix_channel_event_receipts_channel",
        "channel_event_receipts",
        ["channel"],
    )


def downgrade() -> None:
    op.drop_index("ix_channel_event_receipts_channel", table_name="channel_event_receipts")
    op.drop_table("channel_event_receipts")
