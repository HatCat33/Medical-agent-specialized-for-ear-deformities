"""Add bounded rolling memory to conversations.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("conversations")}

    if "memory_summary" not in columns:
        op.add_column(
            "conversations",
            sa.Column("memory_summary", sa.Text(), nullable=False, server_default=""),
        )
    if "memory_through_sequence" not in columns:
        op.add_column(
            "conversations",
            sa.Column(
                "memory_through_sequence",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "memory_updated_at" not in columns:
        op.add_column(
            "conversations",
            sa.Column("memory_updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("conversations", "memory_updated_at")
    op.drop_column("conversations", "memory_through_sequence")
    op.drop_column("conversations", "memory_summary")
