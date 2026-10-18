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
    )
    app = FastAPI(title="Medical Agent API", version="0.2.0", lifespan=lifespan)
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

