"""Structured operational and access-controlled interaction logs."""

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

INTERACTION_LOGGER_NAME = "medical_agent.interactions"


class CalendarJsonlHandler(logging.Handler):
    """Write JSONL into local-calendar folders with bounded parts and retention."""

    terminator = "\n"

    def __init__(
        self,
        root: Path,
        *,
        prefix: str,
        max_bytes: int,
        timezone: str,
        retention_days: int,
    ) -> None:
        super().__init__(logging.INFO)
        try:
            self.timezone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown interaction log timezone: {timezone}") from exc
        self.root = root
        self.prefix = prefix
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self._stream = None
        self._date = ""
        self._part = 0
        self._target: Path | None = None

    def _now(self) -> datetime:
        return datetime.now(self.timezone)

    def _part_number(self, path: Path, date_text: str) -> int | None:
        if path.name == f"{self.prefix}-{date_text}.jsonl":
            return 1
        match = re.fullmatch(
            rf"{re.escape(self.prefix)}-{re.escape(date_text)}-part(\d{{3}})\.jsonl",
            path.name,
        )
        return int(match.group(1)) if match else None

    def _target_for(self, date_text: str, part: int) -> Path:
        year, month, _ = date_text.split("-")
        directory = self.root / year / month
        name = (
            f"{self.prefix}-{date_text}.jsonl"
            if part == 1
            else f"{self.prefix}-{date_text}-part{part:03d}.jsonl"
        )
        return directory / name

    def _latest_part(self, date_text: str) -> int:
        directory = self._target_for(date_text, 1).parent
        parts = [
            part
            for path in directory.glob(f"{self.prefix}-{date_text}*.jsonl")
            if (part := self._part_number(path, date_text)) is not None
        ]
        return max(parts, default=1)

    def _file_date(self, path: Path) -> date | None:
        match = re.fullmatch(
            rf"{re.escape(self.prefix)}-(\d{{4}}-\d{{2}}-\d{{2}})(?:-part\d{{3}})?\.jsonl",
            path.name,
        )
        if match is None:
            return None
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None

    def _remove_expired(self, current_date: date) -> None:
        if self.retention_days <= 0 or not self.root.is_dir():
            return
        cutoff = current_date - timedelta(days=self.retention_days - 1)
        for path in self.root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.jsonl"):
            file_date = self._file_date(path)
            if file_date is not None and file_date < cutoff:
                path.unlink(missing_ok=True)
        for directory in sorted(
            (item for item in self.root.glob("[0-9][0-9][0-9][0-9]/*") if item.is_dir()),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()
        for directory in sorted(
            (item for item in self.root.glob("[0-9][0-9][0-9][0-9]") if item.is_dir()),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()

    def _open(self, date_text: str, part: int) -> None:
        if self._stream is not None:
            self._stream.close()
        target = self._target_for(date_text, part)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._stream = target.open("a", encoding="utf-8")
        self._date = date_text
        self._part = part
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record) + self.terminator
            encoded_size = len(message.encode("utf-8"))
            current_date = self._now().date()
            date_text = current_date.isoformat()
            if self._stream is None or self._date != date_text:
                if self._stream is not None:
                    self._stream.close()
                    self._stream = None
                self._remove_expired(current_date)
                self._open(date_text, self._latest_part(date_text))
            assert self._target is not None
            if (
                self.max_bytes > 0
                and self._target.stat().st_size > 0
                and self._target.stat().st_size + encoded_size > self.max_bytes
            ):
                self._open(date_text, self._part + 1)
            assert self._stream is not None
            self._stream.write(message)
            self._stream.flush()
        except (OSError, TypeError, ValueError, UnicodeError):
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        finally:
            super().close()


class CalendarInteractionHandler(CalendarJsonlHandler):
    """Backward-compatible interaction handler with the standard filename prefix."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        timezone: str,
        retention_days: int = 0,
    ) -> None:
        super().__init__(
            root,
            prefix="interactions",
            max_bytes=max_bytes,
            timezone=timezone,
            retention_days=retention_days,
        )


def calendar_log_root(value: str | Path) -> Path:
    """Map a legacy JSONL setting to its dated-log directory."""
    path = Path(value).resolve()
    return path.parent / path.stem if path.suffix.lower() == ".jsonl" else path


interaction_log_root = calendar_log_root


def _close_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()


def configure_logging(
    *,
    level: str,
    json_logs: bool,
    file_path: str,
    max_bytes: int,
    log_timezone: str,
    log_retention_days: int,
    interaction_enabled: bool,
    interaction_file_path: str,
    interaction_max_bytes: int,
    interaction_timezone: str,
    interaction_retention_days: int,
) -> None:
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    console_renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=console_renderer,
            foreign_pre_chain=shared_processors,
        )
    )

    handlers: list[logging.Handler] = [console_handler]
    if file_path:
        file_handler = CalendarJsonlHandler(
            calendar_log_root(file_path),
            prefix="medical-agent",
            max_bytes=max_bytes,
            timezone=log_timezone,
            retention_days=log_retention_days,
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(ensure_ascii=False),
                foreign_pre_chain=shared_processors,
            )
        )
        handlers.append(file_handler)

    root_logger = logging.getLogger()
    _close_handlers(root_logger)
    root_logger.setLevel(resolved_level)
    for handler in handlers:
        handler.setLevel(resolved_level)
        root_logger.addHandler(handler)

    # httpx INFO records include complete request URLs. Channel media URLs contain
    # short-lived signatures and must not be copied into application logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    interaction_logger = logging.getLogger(INTERACTION_LOGGER_NAME)
    _close_handlers(interaction_logger)
    interaction_logger.setLevel(logging.INFO)
    interaction_logger.propagate = False
    if interaction_enabled and interaction_file_path:
        interaction_handler = CalendarInteractionHandler(
            interaction_log_root(interaction_file_path),
            max_bytes=interaction_max_bytes,
            timezone=interaction_timezone,
            retention_days=interaction_retention_days,
        )
        interaction_handler.setLevel(logging.INFO)
        interaction_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(ensure_ascii=False),
                foreign_pre_chain=shared_processors,
            )
        )
        interaction_logger.addHandler(interaction_handler)
    else:
        interaction_logger.addHandler(logging.NullHandler())


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)


def get_interaction_logger() -> structlog.stdlib.BoundLogger:
    return get_logger(INTERACTION_LOGGER_NAME)
