"""Versioned API router assembly."""

from fastapi import APIRouter

from medical_agent.modules.conversation.router import router as conversation_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(conversation_router)

