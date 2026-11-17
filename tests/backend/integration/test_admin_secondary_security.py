from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from medical_agent.api import admin_security, dependencies
from medical_agent.api.admin_security import router as security_router
from medical_agent.api.dependencies import (
    require_admin_token,
    require_admin_write_access,
)
from medical_agent.core.config import Settings


@pytest_asyncio.fixture
async def protected_admin(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        admin_api_token="primary-token",
        admin_secondary_token="secondary-token",
        admin_secondary_authorization_ttl_seconds=900,
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(admin_security, "get_settings", lambda: settings)

    protected = APIRouter(
        prefix="/api/v1/admin/example",
        dependencies=[Depends(require_admin_token), Depends(require_admin_write_access)],
    )

    @protected.get("")
    async def read_example() -> dict[str, bool]:
        return {"ok": True}

    @protected.post("")
    async def write_example() -> dict[str, bool]:
        return {"saved": True}

    app = FastAPI()
    app.include_router(security_router, prefix="/api/v1")
    app.include_router(protected)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Admin-Token": "primary-token"},
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_secondary_verification_protects_writes_but_not_reads(
    protected_admin: AsyncClient,
) -> None:
    assert (await protected_admin.get("/api/v1/admin/example")).status_code == 200

    rejected = await protected_admin.post("/api/v1/admin/example")
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "admin_secondary_verification_required"

    invalid = await protected_admin.post(
        "/api/v1/admin/security/secondary/verify",
        json={"token": "wrong-token"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "admin_secondary_token_invalid"

    verified = await protected_admin.post(
        "/api/v1/admin/security/secondary/verify",
        json={"token": "secondary-token"},
    )
    assert verified.status_code == 200
    authorization = verified.json()["authorization"]

    accepted = await protected_admin.post(
        "/api/v1/admin/example",
        headers={"X-Admin-Authorization": authorization},
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"saved": True}


@pytest.mark.asyncio
async def test_missing_secondary_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(admin_api_token="primary-token", admin_secondary_token="")
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)

    protected = APIRouter(
        prefix="/api/v1/admin/closed",
        dependencies=[Depends(require_admin_token), Depends(require_admin_write_access)],
    )

    @protected.post("")
    async def write_example() -> dict[str, bool]:
        return {"saved": True}

    app = FastAPI()
    app.include_router(protected)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Admin-Token": "primary-token"},
    ) as client:
        response = await client.post("/api/v1/admin/closed")

    assert response.status_code == 503
    assert response.json()["detail"] == "admin_secondary_token_not_configured"
