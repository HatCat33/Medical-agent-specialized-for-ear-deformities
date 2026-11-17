"""Versioned API router assembly."""

from fastapi import APIRouter, Depends

from medical_agent.api.admin_security import router as admin_security_router
from medical_agent.api.dependencies import require_admin_write_access
from medical_agent.modules.announcements.router import admin_router as announcement_admin_router
from medical_agent.modules.announcements.router import public_router as announcement_public_router
from medical_agent.modules.channels.wechat_kf.router import admin_router as wechat_kf_admin_router
from medical_agent.modules.channels.wechat_kf.router import (
    callback_router as wechat_kf_callback_router,
)
from medical_agent.modules.channels.wecom.router import router as wecom_admin_router
from medical_agent.modules.channels.wecom_h5.router import router as wecom_h5_router
from medical_agent.modules.conversation.router import router as conversation_router
from medical_agent.modules.knowledge.admin_router import router as knowledge_admin_router
from medical_agent.modules.knowledge.router import router as knowledge_router
from medical_agent.modules.media.router import router as media_router
from medical_agent.modules.speech.router import router as speech_router
from medical_agent.modules.system.router import router as system_admin_router

api_router = APIRouter(prefix="/api/v1")
admin_write_dependencies = [Depends(require_admin_write_access)]
api_router.include_router(announcement_public_router)
api_router.include_router(admin_security_router)
api_router.include_router(announcement_admin_router, dependencies=admin_write_dependencies)
api_router.include_router(conversation_router)
api_router.include_router(knowledge_router, dependencies=admin_write_dependencies)
api_router.include_router(knowledge_admin_router, dependencies=admin_write_dependencies)
api_router.include_router(media_router)
api_router.include_router(speech_router)
api_router.include_router(system_admin_router, dependencies=admin_write_dependencies)
api_router.include_router(wecom_admin_router, dependencies=admin_write_dependencies)
api_router.include_router(wecom_h5_router)
api_router.include_router(wechat_kf_callback_router)
api_router.include_router(wechat_kf_admin_router, dependencies=admin_write_dependencies)

