from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from medical_agent.api.router import api_router
from medical_agent.core.config import get_settings
from medical_agent.core.database import close_db, init_db
from medical_agent.core.logging import configure_logging
from medical_agent.core.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    # The embedded job runner is for one API process. Recover interrupted jobs
    # on startup; completed batches remain available for explicit retry.
    from sqlalchemy import update

    from medical_agent.core.database import session_factory
    from medical_agent.modules.knowledge.models import KnowledgeOperation

    async with session_factory() as db:
        await db.execute(
            update(KnowledgeOperation)
            .where(KnowledgeOperation.active_slot.is_not(None))
            .values(status="interrupted", active_slot=None, error="server_restarted_retry_required")
        )
        await db.commit()
    yield
    await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        json_logs=settings.log_json,
        file_path=settings.log_file_path,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        interaction_enabled=settings.interaction_log_enabled,
        interaction_file_path=settings.interaction_log_file_path,
        interaction_max_bytes=settings.interaction_log_max_bytes,
        interaction_backup_count=settings.interaction_log_backup_count,
    )
    app = FastAPI(title="Medical Agent API", version="0.3.0", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    @app.get("/api/v1/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok", "mode": settings.llm_provider}

    return app


app = create_app()
