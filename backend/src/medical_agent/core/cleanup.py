"""Bounded resource cleanup that survives HTTP disconnect cancellation."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import anyio

from medical_agent.core.logging import get_logger

logger = get_logger(__name__)
_pending: set[asyncio.Task[None]] = set()


async def complete_cleanup(operation: Coroutine[Any, Any, object]) -> None:
    """Finish cleanup even if Starlette or a repeated Task.cancel interrupts us.

    AnyIO shielding handles disconnect scopes; a separate, strongly referenced
    task also survives explicit asyncio cancellation. The deadline prevents a
    broken network close from leaving an unbounded background task.
    """
    async def bounded() -> None:
        async with asyncio.timeout(5):
            await operation

    task = asyncio.create_task(bounded())
    _pending.add(task)

    def finished(result: asyncio.Task[None]) -> None:
        _pending.discard(result)
        if not result.cancelled() and (error := result.exception()) is not None:
            logger.warning("resource_cleanup_failed", error_type=type(error).__name__)

    task.add_done_callback(finished)
    with anyio.CancelScope(shield=True):
        await asyncio.shield(task)
