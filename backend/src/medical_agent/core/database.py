"""Async database engine, sessions, and transaction helpers."""

from collections.abc import AsyncIterator
from importlib import import_module

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
    import_module("medical_agent.modules.media.models")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()

