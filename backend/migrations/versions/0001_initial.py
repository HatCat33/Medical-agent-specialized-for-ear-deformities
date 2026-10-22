"""Baseline existing v0.3 tables and add durable knowledge operations.

Existing SQLite tables are retained. Subsequent revisions must use explicit
add/alter operations rather than changing this baseline.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The application's pre-existing tables are captured in the initial metadata
    # below; this snapshot is independent of future model changes.
    from medical_agent.migrations_baseline import baseline_metadata

    baseline_metadata().create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Baseline downgrade is intentionally disabled; restore a database backup.")
