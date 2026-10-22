"""Exercise the deployed 0002 -> 0003 upgrade without touching application data."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from medical_agent.modules.safety.prompt import SAFETY_SYSTEM_PROMPT


@pytest.mark.parametrize("partial", ["none", "settings_created", "column_added"])
def test_existing_prompts_survive_system_text_migration(tmp_path: Path, partial: str) -> None:
    database = tmp_path / "migration.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}"}
    backend = Path(__file__).resolve().parents[2]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0002"],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
    )
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.begin() as connection:
            if partial != "none":
                connection.execute(
                    text(
                        "CREATE TABLE runtime_settings (key VARCHAR(80) PRIMARY KEY, value_json TEXT NOT NULL)"
                    )
                )
                connection.execute(
                    text("INSERT INTO runtime_settings VALUES ('speech', :value)"),
                    {"value": '{"rate":1.2}'},
                )
            if partial == "column_added":
                connection.execute(
                    text(
                        "ALTER TABLE prompt_releases ADD COLUMN system_content TEXT NOT NULL DEFAULT 'retained custom system'"
                    )
                )
            connection.execute(
                text(
                    "INSERT INTO prompt_releases (id, name, content, checksum, status, created_at) "
                    "VALUES ('old', 'existing', 'business', :checksum, 'published', CURRENT_TIMESTAMP)"
                ),
                {"checksum": "a" * 64},
            )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend,
            env=environment,
            check=True,
            capture_output=True,
        )
        with engine.connect() as connection:
            row = (
                connection.execute(text("SELECT * FROM prompt_releases WHERE id='old'"))
                .mappings()
                .one()
            )
            assert row["content"] == "business"
            expected = (
                "retained custom system" if partial == "column_added" else SAFETY_SYSTEM_PROMPT
            )
            assert row["system_content"] == expected
            assert (
                row["checksum"]
                == hashlib.sha256((expected + "\n\nbusiness").encode("utf-8")).hexdigest()
            )
            assert connection.execute(text("SELECT count(*) FROM runtime_settings")).scalar() == (
                0 if partial == "none" else 1
            )
    finally:
        engine.dispose()
