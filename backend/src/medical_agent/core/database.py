"""Async database engine, sessions, and transaction helpers."""

from collections.abc import AsyncIterator
from importlib import import_module

from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from medical_agent.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def init_db() -> None:
    # Import models here so SQLAlchemy sees them before creating development tables.
    import_module("medical_agent.modules.conversation.models")
    import_module("medical_agent.modules.channels.common.models")
    import_module("medical_agent.modules.knowledge.models")
    import_module("medical_agent.modules.media.models")
    import_module("medical_agent.modules.system.models")

    def verify_existing_schema(connection: Connection) -> None:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        missing: list[str] = []
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            columns = {item["name"] for item in inspector.get_columns(table.name)}
            missing.extend(
                f"{table.name}.{column.name}"
                for column in table.columns
                if column.name not in columns
            )
        if missing:
            raise RuntimeError(
                "Database migration required (" + ", ".join(missing) + "). "
                "Run in backend: uv run python scripts/upgrade_database.py; then restart the API."
            )

    async with engine.connect() as connection:
        await connection.run_sync(verify_existing_schema)

    if settings.app_env == "production":
        # Production schemas are changed explicitly with `alembic upgrade head`.
        return

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()
