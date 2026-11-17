"""Add monotonic system counters.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("system_counters"):
        op.create_table(
            "system_counters",
            sa.Column("key", sa.String(length=80), nullable=False),
            sa.Column("value", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("key"),
        )

    interaction_count = 0
    if inspector.has_table("messages") and inspector.has_table("conversations"):
        interaction_count = int(
            connection.execute(
                sa.text(
                    "SELECT COUNT(messages.id) FROM messages "
                    "JOIN conversations ON conversations.id = messages.conversation_id "
                    "WHERE messages.role = :role "
                    "AND conversations.owner_id NOT LIKE :admin_owner"
                ),
                {"role": "user", "admin_owner": "admin-test-%"},
            ).scalar_one()
            or 0
        )
    existing = connection.execute(
        sa.text("SELECT value FROM system_counters WHERE key = :key"),
        {"key": "patient_interactions"},
    ).scalar_one_or_none()
    if existing is not None:
        if int(existing) < interaction_count:
            connection.execute(
                sa.text(
                    "UPDATE system_counters SET value = :value, "
                    "updated_at = CURRENT_TIMESTAMP WHERE key = :key"
                ),
                {"key": "patient_interactions", "value": interaction_count},
            )
        return

    connection.execute(
        sa.text(
            "INSERT INTO system_counters (key, value, updated_at) "
            "VALUES (:key, :value, CURRENT_TIMESTAMP)"
        ),
        {"key": "patient_interactions", "value": interaction_count},
    )


def downgrade() -> None:
    op.drop_table("system_counters")
