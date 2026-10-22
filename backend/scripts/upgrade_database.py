"""Back up the configured SQLite database, then apply versioned migrations."""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from medical_agent.core.config import get_settings


def main() -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        source = Path(url.database).resolve()
        if source.is_file():
            directory = source.parent / "backups"
            directory.mkdir(exist_ok=True)
            snapshot = directory / f"{source.stem}-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
            with (
                sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as origin,
                sqlite3.connect(snapshot) as destination,
            ):
                origin.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError(
                        "Database backup integrity check failed; migration cancelled"
                    )
            print(f"Verified SQLite backup: {snapshot}")
    backend = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env={**os.environ, "DATABASE_URL": settings.database_url},
        check=True,
    )
    print("Database migrations completed.")


if __name__ == "__main__":
    main()
