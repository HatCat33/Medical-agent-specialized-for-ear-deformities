"""Atomic monotonic counters used by administrative statistics."""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.modules.conversation.models import Conversation, Message
from medical_agent.modules.system.models import SystemCounter

PATIENT_INTERACTIONS = "patient_interactions"


async def _existing_patient_interactions(db: AsyncSession) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.role == "user",
            ~Conversation.owner_id.like("admin-test-%"),
        )
    )
    return int(count or 0)


async def increment(db: AsyncSession, key: str) -> None:
    """Increment a counter atomically, creating it for fresh development databases."""
    now = datetime.now(UTC)
    dialect = db.bind.dialect.name if db.bind else ""
    initial_value = (
        await _existing_patient_interactions(db) if key == PATIENT_INTERACTIONS else 1
    )
    values = {"key": key, "value": initial_value, "updated_at": now}
    if dialect == "postgresql":
        statement = postgresql_insert(SystemCounter).values(**values)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[SystemCounter.key],
                set_={"value": SystemCounter.value + 1, "updated_at": now},
            )
        )
        return
    if dialect == "sqlite":
        statement = sqlite_insert(SystemCounter).values(**values)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[SystemCounter.key],
                set_={"value": SystemCounter.value + 1, "updated_at": now},
            )
        )
        return

    result = await db.execute(
        update(SystemCounter)
        .where(SystemCounter.key == key)
        .values(value=SystemCounter.value + 1, updated_at=now)
    )
    if result.rowcount == 0:
        db.add(SystemCounter(**values))


async def value(db: AsyncSession, key: str) -> int:
    result = await db.scalar(select(SystemCounter.value).where(SystemCounter.key == key))
    if result is not None:
        return int(result)
    if key == PATIENT_INTERACTIONS:
        return await _existing_patient_interactions(db)
    return 0
