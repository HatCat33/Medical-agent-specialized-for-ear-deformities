"""Structured operational and access-controlled interaction logs."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

INTERACTION_LOGGER_NAME = "medical_agent.interactions"


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
    backup_count: int,
    interaction_enabled: bool,
    interaction_file_path: str,
    interaction_max_bytes: int,
    interaction_backup_count: int,
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
        target = Path(file_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            target,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
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

    interaction_logger = logging.getLogger(INTERACTION_LOGGER_NAME)
    _close_handlers(interaction_logger)
    interaction_logger.setLevel(logging.INFO)
    interaction_logger.propagate = False
    if interaction_enabled and interaction_file_path:
        interaction_target = Path(interaction_file_path).resolve()
        interaction_target.parent.mkdir(parents=True, exist_ok=True)
        interaction_handler = RotatingFileHandler(
            interaction_target,
            maxBytes=interaction_max_bytes,
            backupCount=interaction_backup_count,
            encoding="utf-8",
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
