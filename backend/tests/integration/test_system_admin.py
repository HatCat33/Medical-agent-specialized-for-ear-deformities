"""Prompt releases and model operations exposed by the protected admin API."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from medical_agent.api import dependencies
from medical_agent.api.router import api_router
from medical_agent.core.config import Settings
from medical_agent.core.database import Base, get_db
from medical_agent.modules.speech import router as speech_router
from medical_agent.modules.system import router as system_router
from medical_agent.modules.system import speech_service
from medical_agent.modules.system.schemas import SpeechSettings

PREFIX = "/api/v1/admin/system"


@pytest.fixture
async def system_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'system-admin.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings.model_construct(
        admin_api_token="test-admin",
        llm_provider="demo",
        llm_api_key="chat-secret-value",
        vision_base_url="https://vision.example/v1",
        vision_api_key="vision-secret-value",
        vision_model="vision-model",
        embedding_api_key="embedding-secret-value",
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(system_router, "get_settings", lambda: settings)

    async def database() -> AsyncIterator[AsyncSession]:
        async with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = database
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Admin-Token": "test-admin"},
    ) as client:
        yield client
    await engine.dispose()


async def create_prompt(client: AsyncClient, name: str, content: str) -> dict[str, Any]:
    response = await client.post(f"{PREFIX}/prompts", json={"name": name, "content": content})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_prompt_draft_publish_and_rollback(system_admin: AsyncClient) -> None:
    initial = await system_admin.get(f"{PREFIX}/prompts")
    assert initial.status_code == 200
    assert initial.json()["using_default"] is True
    assert "医疗安全约束" in initial.json()["safety_baseline"]

    first = await create_prompt(system_admin, "首个版本", "首个业务提示词")
    updated = await system_admin.put(
        f"{PREFIX}/prompts/{first['id']}",
        json={"name": "首个版本（已检查）", "content": "首个业务提示词（已检查）"},
    )
    assert updated.status_code == 200
    assert (await system_admin.post(f"{PREFIX}/prompts/{first['id']}/publish")).status_code == 200

    second = await create_prompt(system_admin, "第二个版本", "第二个业务提示词")
    assert (await system_admin.post(f"{PREFIX}/prompts/{second['id']}/publish")).status_code == 200
    rolled_back = await system_admin.post(f"{PREFIX}/prompts/{first['id']}/publish")
    assert rolled_back.status_code == 200
    assert rolled_back.json()["status"] == "published"

    inventory = (await system_admin.get(f"{PREFIX}/prompts")).json()
    by_id = {item["id"]: item for item in inventory["items"]}
    assert by_id[first["id"]]["status"] == "published"
    assert by_id[second["id"]]["status"] == "archived"
    assert inventory["effective_content"] == "首个业务提示词（已检查）"
    assert (
        await system_admin.put(
            f"{PREFIX}/prompts/{first['id']}",
            json={"name": "不可编辑", "content": "不可编辑"},
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_model_inventory_masks_secrets_and_manual_probe_is_audited(
    system_admin: AsyncClient,
) -> None:
    response = await system_admin.get(f"{PREFIX}/models")
    assert response.status_code == 200
    assert "chat-secret-value" not in response.text
    assert "vision-secret-value" not in response.text
    assert "embedding-secret-value" not in response.text
    cards = {item["kind"]: item for item in response.json()["items"]}
    assert cards["chat"]["model"] == "内置演示"
    assert cards["chat"]["endpoint_host"] == "本地进程"
    assert cards["chat"]["api_key_configured"] is False
    assert cards["vision"]["endpoint_host"] == "https://vision.example"
    assert cards["tts"]["client_only"] is False
    assert cards["browser_tts"]["client_only"] is True

    probe = await system_admin.post(f"{PREFIX}/models/chat/probe")
    assert probe.status_code == 200
    assert probe.json()["status"] == "healthy"
    after = (await system_admin.get(f"{PREFIX}/models")).json()
    chat = next(item for item in after["items"] if item["kind"] == "chat")
    assert chat["last_probe"]["id"] == probe.json()["id"]


@pytest.mark.asyncio
async def test_system_prompt_and_speech_require_admin_and_persist(
    system_admin: AsyncClient,
) -> None:
    for path in ("prompts", "speech", "models"):
        denied = await system_admin.get(f"{PREFIX}/{path}", headers={"X-Admin-Token": "wrong"})
        assert denied.status_code == 401
    payload = {"name": "自定义系统", "content": "业务表达", "system_content": "系统指令版本一"}
    denied_write = await system_admin.post(
        f"{PREFIX}/prompts", json=payload, headers={"X-Admin-Token": "wrong"}
    )
    assert denied_write.status_code == 401
    created = await system_admin.post(f"{PREFIX}/prompts", json=payload)
    assert created.status_code == 201
    first = created.json()
    updated = await system_admin.put(
        f"{PREFIX}/prompts/{first['id']}", json={**payload, "system_content": "系统指令版本二"}
    )
    assert updated.json()["checksum"] != first["checksum"]
    await system_admin.post(f"{PREFIX}/prompts/{first['id']}/publish")
    inventory = (await system_admin.get(f"{PREFIX}/prompts")).json()
    assert inventory["effective_system_content"] == "系统指令版本二"
    assert inventory["effective"]["system_content"] == "系统指令版本二"

    settings = {
        "auto_play": False,
        "provider": "browser",
        "cloud_voice": "",
        "voice": "test-voice",
        "language": "zh-CN",
        "rate": 1.25,
        "pitch": 0.8,
        "volume": 0.6,
    }
    denied = await system_admin.put(
        f"{PREFIX}/speech", json=settings, headers={"X-Admin-Token": "wrong"}
    )
    assert denied.status_code == 401
    assert (await system_admin.put(f"{PREFIX}/speech", json=settings)).json() == settings
    public = await system_admin.get("/api/v1/speech/settings", headers={"X-Admin-Token": ""})
    assert public.status_code == 200
    assert public.json() == settings
    assert (await system_admin.get(f"{PREFIX}/speech")).json() == settings
    invalid = await system_admin.put(f"{PREFIX}/speech", json={**settings, "rate": 100})
    assert invalid.status_code == 422
    operations = (await system_admin.get(f"{PREFIX}/operations")).json()
    assert any(item["kind"] == "speech_settings_updated" for item in operations["items"])


@pytest.mark.asyncio
async def test_speech_preview_and_custom_provider_permissions(system_admin: AsyncClient) -> None:
    response = await system_admin.post(
        f"{PREFIX}/speech/preview",
        headers={"X-Admin-Token": "bad"},
        json={"text": "测试", "settings": {"cloud_voice": "custom"}},
    )
    assert response.status_code == 401
    response = await system_admin.post("/api/v1/speech/syntheses", json={"text": "测试"})
    assert response.status_code == 401
    invalid = await system_admin.post(
        "/api/v1/speech/syntheses",
        headers={"X-Visitor-ID": "test-visitor-123"},
        json={"text": "a" * 601},
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_custom_voice_saved_used_by_patient_and_shown_in_inventory(
    system_admin: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_construct(
        tts_base_url="https://tts.example/v1",
        tts_api_key="tts-secret",
        tts_model="custom-voice-model",
    )
    monkeypatch.setattr(speech_service, "get_settings", lambda: settings)
    monkeypatch.setattr(system_router, "get_settings", lambda: settings)
    monkeypatch.setattr(speech_router, "get_settings", lambda: settings)
    response = await system_admin.put(
        f"{PREFIX}/speech", json={"provider": "openai-compatible", "cloud_voice": "cloned:example"}
    )
    assert response.status_code == 200
    received: list[str] = []

    async def fake_synthesis(text: str, preferences: SpeechSettings, settings: Settings) -> bytes:
        received.extend([text, preferences.cloud_voice, settings.tts_model])
        return b"ID3sample"

    monkeypatch.setattr(speech_router, "synthesize", fake_synthesis)
    response = await system_admin.post(
        "/api/v1/speech/syntheses",
        headers={"X-Visitor-ID": "test-visitor-voice"},
        json={"text": "患者回答播报"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b"ID3sample"
    assert received == ["患者回答播报", "cloned:example", "custom-voice-model"]
    inventory = await system_admin.get(f"{PREFIX}/models")
    assert "tts-secret" not in inventory.text
    card = next(item for item in inventory.json()["items"] if item["kind"] == "tts")
    assert card["client_only"] is False
    assert card["model"] == "custom-voice-model"


@pytest.mark.asyncio
async def test_cloud_inventory_and_probe_available_while_browser_selected(
    system_admin: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from medical_agent.modules.system import model_service

    settings = Settings.model_construct(
        tts_base_url="https://api.siliconflow.cn/v1",
        tts_api_key="private-key",
        tts_model="FunAudioLLM/CosyVoice2-0.5B",
        tts_voice="anna",
    )
    monkeypatch.setattr(system_router, "get_settings", lambda: settings)
    received: list[str] = []

    async def fake_synthesis(text: str, preferences: SpeechSettings, settings: Settings) -> bytes:
        received.append(preferences.cloud_voice or settings.tts_voice)
        return b"ID3test"

    monkeypatch.setattr(model_service, "synthesize", fake_synthesis)
    items = (await system_admin.get(f"{PREFIX}/models")).json()["items"]
    cloud = next(item for item in items if item["kind"] == "tts")
    assert cloud["model"] == settings.tts_model
    assert cloud["active"] is False
    assert cloud["settings"]["voice"] == settings.tts_model + ":anna"
    assert (await system_admin.post(f"{PREFIX}/models/tts/probe")).json()["status"] == "healthy"
    draft = await system_admin.post(f"{PREFIX}/speech/probe", json={"cloud_voice": "custom:voice"})
    assert draft.status_code == 200
    assert received == ["anna", "custom:voice"]
    assert (await system_admin.get(f"{PREFIX}/speech")).json()["provider"] == "browser"


@pytest.mark.asyncio
async def test_preview_reports_sanitized_failure_and_audits_it(
    system_admin: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from fastapi import HTTPException

    settings = Settings.model_construct(tts_api_key="private-key", tts_model="tts-test")
    monkeypatch.setattr(system_router, "get_settings", lambda: settings)

    async def failed(text: str, preferences: SpeechSettings, settings: Settings) -> bytes:
        response = httpx.Response(
            400,
            request=httpx.Request("POST", "https://tts.example"),
            json={"code": 20047, "message": "Invalid voice private-key"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(502, "tts_request_failed") from exc
        return b""

    monkeypatch.setattr(system_router, "synthesize", failed)
    response = await system_admin.post(
        f"{PREFIX}/speech/preview", json={"text": "测试", "settings": {"cloud_voice": "custom"}}
    )
    assert response.status_code == 502
    assert response.json()["detail"]["http_status"] == 400
    assert "private-key" not in response.text
    operations = await system_admin.get(f"{PREFIX}/operations")
    assert "speech_preview_failed" in operations.text
    assert "private-key" not in operations.text
