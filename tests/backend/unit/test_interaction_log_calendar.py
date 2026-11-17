import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from medical_agent.core.logging import (
    CalendarInteractionHandler,
    CalendarJsonlHandler,
    interaction_log_root,
)


def test_calendar_handler_creates_daily_files_and_size_parts(
    monkeypatch, tmp_path: Path
) -> None:
    handler = CalendarInteractionHandler(
        tmp_path / "interactions",
        max_bytes=6,
        timezone="Asia/Shanghai",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    current = datetime(2026, 11, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(handler, "_now", lambda: current)

    handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "1234", (), None))
    handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "5678", (), None))
    current = datetime(2026, 11, 13, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "9012", (), None))
    handler.close()

    first = tmp_path / "interactions" / "2026" / "11" / "interactions-2026-11-12.jsonl"
    second = (
        tmp_path
        / "interactions"
        / "2026"
        / "11"
        / "interactions-2026-11-12-part002.jsonl"
    )
    next_day = tmp_path / "interactions" / "2026" / "11" / "interactions-2026-11-13.jsonl"
    assert first.read_text(encoding="utf-8") == "1234\n"
    assert second.read_text(encoding="utf-8") == "5678\n"
    assert next_day.read_text(encoding="utf-8") == "9012\n"


def test_legacy_log_path_maps_to_dated_directory(tmp_path: Path) -> None:
    assert interaction_log_root(tmp_path / "interactions.jsonl") == tmp_path / "interactions"


def test_calendar_handler_removes_only_expired_dated_files(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "medical-agent"
    old = root / "2026" / "10" / "medical-agent-2026-10-01.jsonl"
    retained = root / "2026" / "11" / "medical-agent-2026-11-11.jsonl"
    unrelated = root / "legacy.jsonl"
    for path in (old, retained, unrelated):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("existing\n", encoding="utf-8")
    handler = CalendarJsonlHandler(
        root,
        prefix="medical-agent",
        max_bytes=1024,
        timezone="Asia/Shanghai",
        retention_days=2,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    monkeypatch.setattr(
        handler,
        "_now",
        lambda: datetime(2026, 11, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "new", (), None))
    handler.close()

    assert not old.exists()
    assert retained.exists()
    assert unrelated.exists()
    assert (root / "2026" / "11" / "medical-agent-2026-11-12.jsonl").exists()
