"""Versioned API router assembly."""

from fastapi import APIRouter

from medical_agent.modules.conversation.router import router as conversation_router
from medical_agent.modules.knowledge.admin_router import router as knowledge_admin_router
from medical_agent.modules.knowledge.router import router as knowledge_router
from medical_agent.modules.media.router import router as media_router
from medical_agent.modules.speech.router import router as speech_router
from medical_agent.modules.system.router import router as system_admin_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(conversation_router)
api_router.include_router(knowledge_router)
api_router.include_router(knowledge_admin_router)
api_router.include_router(media_router)
api_router.include_router(speech_router)
api_router.include_router(system_admin_router)

