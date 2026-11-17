from medical_agent.core.config import Settings
from medical_agent.core.database import Base, database_engine_options, load_model_metadata


def test_postgres_engine_uses_configured_pool_limits() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:password@localhost:5432/medical_agent",
        database_pool_size=12,
        database_max_overflow=18,
        database_pool_timeout_seconds=15,
        database_pool_recycle_seconds=900,
        database_pool_pre_ping=True,
    )

    assert database_engine_options(settings) == {
        "echo": False,
        "pool_size": 12,
        "max_overflow": 18,
        "pool_timeout": 15,
        "pool_recycle": 900,
        "pool_pre_ping": True,
    }


def test_sqlite_does_not_receive_postgres_pool_tuning() -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite:///./test.db",
        database_pool_size=99,
        database_max_overflow=99,
    )

    assert database_engine_options(settings) == {"echo": False}


def test_model_metadata_includes_channel_persistence_tables() -> None:
    load_model_metadata()

    table_names = set(Base.metadata.tables)
    assert "channel_event_receipts" in table_names
    assert "channel_sync_cursors" in table_names
