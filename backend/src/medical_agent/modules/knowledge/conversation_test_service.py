"""Run disposable admin multi-turn tests through the patient conversation path."""

import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.modules.conversation import repository
from medical_agent.modules.conversation import service as conversation_service
from medical_agent.modules.knowledge.admin_schemas import ConversationTestRequest
from medical_agent.modules.system.prompt_service import audit


async def run(db: AsyncSession, payload: ConversationTestRequest) -> dict[str, Any]:
    test_id = str(uuid.uuid4())
    owner_id = f"admin-test-{test_id}"
    conversation = await repository.create_conversation(
        db, owner_id=owner_id, title="管理端多轮测试", conversation_id=test_id
    )
    await db.commit()
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    try:
        for index, turn in enumerate(payload.turns, start=1):
            diagnostics: dict[str, object] = {}
            user_message = None
            assistant_message = None
            async for event, value in conversation_service.stream_message(
                db,
                conversation=conversation,
                content=turn.content,
                input_mode="text",
                transport="admin_test",
                record_interaction=False,
                diagnostics=diagnostics,
            ):
                if event == "user_message" and not isinstance(value, str):
                    user_message = value
                elif event == "done" and not isinstance(value, str):
                    assistant_message = value
            if user_message is None or assistant_message is None:
                raise RuntimeError("conversation_test_generation_incomplete")
            results.append(
                {
                    "turn": index,
                    "input": user_message.content,
                    "output": assistant_message.content,
                    "status": assistant_message.status,
                    "safety_code": assistant_message.safety_code,
                    "user_message_id": user_message.id,
                    "assistant_message_id": assistant_message.id,
                    **diagnostics,
                }
            )
    finally:
        await db.rollback()
        await repository.delete_conversation(db, conversation_id=test_id, owner_id=owner_id)
        await db.commit()
    total_ms = round((time.perf_counter() - started) * 1000, 2)
    audit(
        db,
        kind="conversation_test",
        target_id=test_id,
        status="completed",
        turn_count=len(results),
        duration_ms=total_ms,
    )
    await db.commit()
    return {
        "id": test_id,
        "turn_count": len(results),
        "duration_ms": total_ms,
        "turns": results,
    }
