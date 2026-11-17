import base64
import hashlib
import json
import struct
from pathlib import Path
from typing import cast

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medical_agent.core.config import Settings
from medical_agent.core.database import Base
from medical_agent.modules.channels.common.models import ChannelEventReceipt, ChannelSyncCursor
from medical_agent.modules.channels.wechat_kf import service as wechat_kf_service
from medical_agent.modules.channels.wechat_kf.client import WeChatKfClient, split_utf8
from medical_agent.modules.channels.wechat_kf.crypto import (
    WeChatKfCallbackCrypto,
    WeChatKfCryptoError,
    parse_callback_event,
)
from medical_agent.modules.channels.wechat_kf.service import WeChatKfRuntime
from medical_agent.modules.conversation import service as conversation_service
from medical_agent.modules.conversation.models import Conversation, Message
from medical_agent.modules.media.models import MediaObject


def _encrypted_callback(
    *,
    aes_key: bytes,
    corp_id: str,
    xml_text: str,
) -> str:
    message = xml_text.encode()
    plaintext = b"0123456789abcdef" + struct.pack("!I", len(message)) + message + corp_id.encode()
    padding = 32 - (len(plaintext) % 32)
    padded = plaintext + bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16])).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def _signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    return hashlib.sha1("".join(sorted((token, timestamp, nonce, encrypted))).encode()).hexdigest()


def test_wechat_kf_callback_crypto_verifies_signature_receiver_and_event() -> None:
    key = bytes(range(32))
    encoded_key = base64.b64encode(key).decode().rstrip("=")
    token = "callback-token"
    corp_id = "ww-corp"
    xml = (
        "<xml><ToUserName>ww-corp</ToUserName><Event>kf_msg_or_event</Event>"
        "<Token>sync-token</Token><OpenKfId>wk-1</OpenKfId></xml>"
    )
    encrypted = _encrypted_callback(aes_key=key, corp_id=corp_id, xml_text=xml)
    signature = _signature(token, "100", "nonce", encrypted)
    crypto = WeChatKfCallbackCrypto(
        token=token,
        encoding_aes_key=encoded_key,
        corp_id=corp_id,
    )

    decrypted = crypto.verify_and_decrypt(
        signature=signature,
        timestamp="100",
        nonce="nonce",
        encrypted=encrypted,
    )
    event = parse_callback_event(decrypted)
    assert event.open_kfid == "wk-1"
    assert event.token == "sync-token"

    with pytest.raises(WeChatKfCryptoError, match="signature_invalid"):
        crypto.verify_and_decrypt(
            signature="invalid",
            timestamp="100",
            nonce="nonce",
            encrypted=encrypted,
        )

    wrong_receiver = WeChatKfCallbackCrypto(
        token=token,
        encoding_aes_key=encoded_key,
        corp_id="ww-other",
    )
    with pytest.raises(WeChatKfCryptoError, match="receiver_invalid"):
        wrong_receiver.verify_and_decrypt(
            signature=signature,
            timestamp="100",
            nonce="nonce",
            encrypted=encrypted,
        )


@pytest.mark.asyncio
async def test_wechat_kf_url_verification_can_start_before_open_kfid_is_known() -> None:
    key = base64.b64encode(bytes(range(32))).decode().rstrip("=")
    settings = Settings(
        wechat_kf_enabled=True,
        wechat_kf_corp_id="ww-corp",
        wechat_kf_secret="secret",
        wechat_kf_callback_token="callback-token",
        wechat_kf_callback_aes_key=key,
    )
    runtime = WeChatKfRuntime()

    await runtime.start(settings)

    assert runtime.callback_crypto() is not None
    assert runtime.status.configured is False
    assert runtime.status.state == "waiting_for_open_kfid"
    assert runtime.status.last_error == "wechat_kf_open_kfid_missing"
    await runtime.stop()


@pytest.mark.asyncio
async def test_wechat_kf_client_caches_token_syncs_sends_and_downloads_media() -> None:
    token_requests = 0
    sent_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url.path == "/cgi-bin/gettoken":
            token_requests += 1
            return httpx.Response(200, json={"errcode": 0, "access_token": "token", "expires_in": 7200})
        assert request.url.params.get("access_token") == "token"
        if request.url.path == "/cgi-bin/kf/sync_msg":
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "next_cursor": "cursor-2",
                    "has_more": 0,
                    "msg_list": [{"msgid": "message-1", "msgtype": "text"}],
                },
            )
        if request.url.path == "/cgi-bin/kf/send_msg":
            sent_payloads.append(cast(dict[str, object], json.loads(request.content)))
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
        if request.url.path == "/cgi-bin/media/get":
            return httpx.Response(200, content=b"#!AMR\nvoice")
        return httpx.Response(404)

    settings = Settings(
        wechat_kf_corp_id="ww-corp",
        wechat_kf_secret="secret",
        wechat_kf_api_base_url="https://api.test",
    )
    client = WeChatKfClient(settings, transport=httpx.MockTransport(handler))
    next_cursor, has_more, messages = await client.sync_messages(
        open_kfid="wk-1",
        callback_token="sync-token",
        cursor="",
    )
    await client.send_text(
        open_kfid="wk-1",
        external_userid="wm-user",
        content="耳" * 1000,
    )
    media = await client.download_media("media-1", max_bytes=1024)

    assert next_cursor == "cursor-2"
    assert has_more is False
    assert messages[0]["msgid"] == "message-1"
    assert token_requests == 1
    assert len(sent_payloads) == 2
    assert all(
        len(cast(dict[str, str], item["text"])["content"].encode("utf-8")) <= 2048
        for item in sent_payloads
    )
    assert media == b"#!AMR\nvoice"
    assert "".join(split_utf8("耳" * 1000)) == "耳" * 1000


@pytest.mark.asyncio
async def test_wechat_kf_text_message_uses_stable_conversation_and_deduplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wechat-kf.db'}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    settings = Settings(
        llm_provider="demo",
        rag_enabled=False,
        media_storage_path=str(tmp_path / "media"),
        wechat_kf_enabled=True,
        wechat_kf_corp_id="ww-corp",
        wechat_kf_secret="secret",
        wechat_kf_open_kfid="wk-1",
        wechat_kf_callback_token="callback-token",
        wechat_kf_callback_aes_key=base64.b64encode(bytes(range(32))).decode().rstrip("="),
    )
    monkeypatch.setattr(wechat_kf_service, "session_factory", session_maker)
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)

    replies: list[str] = []
    cursors: list[str] = []

    class FakeClient:
        async def sync_messages(
            self,
            *,
            open_kfid: str,
            callback_token: str,
            cursor: str,
        ) -> tuple[str, bool, list[dict[str, object]]]:
            assert open_kfid == "wk-1"
            assert callback_token == "sync-token"
            cursors.append(cursor)
            return "cursor-1", False, [payload]

        async def send_text(self, *, open_kfid: str, external_userid: str, content: str) -> None:
            assert open_kfid == "wk-1"
            assert external_userid == "wm-user"
            replies.append(content)

    runtime = WeChatKfRuntime()
    runtime._settings = settings  # pyright: ignore[reportPrivateUsage]
    runtime._client = cast(WeChatKfClient, FakeClient())  # pyright: ignore[reportPrivateUsage]
    payload: dict[str, object] = {
        "msgid": "message-1",
        "open_kfid": "wk-1",
        "external_userid": "wm-user",
        "origin": 3,
        "msgtype": "text",
        "text": {"content": "第一次就诊需要准备什么？"},
    }

    await runtime._sync(open_kfid="wk-1", callback_token="sync-token")  # pyright: ignore[reportPrivateUsage]
    await runtime._sync(open_kfid="wk-1", callback_token="sync-token")  # pyright: ignore[reportPrivateUsage]

    assert len(replies) == 1
    assert cursors == ["", "cursor-1"]
    assert "Demo 测试模式" in replies[0]
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 1
        assert await session.scalar(select(func.count()).select_from(Message)) == 2
        assert await session.scalar(select(func.count()).select_from(ChannelEventReceipt)) == 1
        assert await session.scalar(select(ChannelSyncCursor.cursor)) == "cursor-1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_wechat_kf_image_is_stored_as_owned_conversation_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wechat-kf-image.db'}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    settings = Settings(
        llm_provider="demo",
        rag_enabled=False,
        media_storage_path=str(tmp_path / "media"),
        wechat_kf_enabled=True,
        wechat_kf_open_kfid="wk-1",
    )
    monkeypatch.setattr(wechat_kf_service, "session_factory", session_maker)
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)

    class FakeClient:
        async def download_media(self, media_id: str, *, max_bytes: int) -> bytes:
            assert media_id == "image-media"
            assert max_bytes > 0
            return b"\x89PNG\r\n\x1a\nwechat-kf-image"

        async def send_text(self, *, open_kfid: str, external_userid: str, content: str) -> None:
            assert content

    runtime = WeChatKfRuntime()
    runtime._settings = settings  # pyright: ignore[reportPrivateUsage]
    runtime._client = cast(WeChatKfClient, FakeClient())  # pyright: ignore[reportPrivateUsage]
    await runtime._process_message(  # pyright: ignore[reportPrivateUsage]
        {
            "msgid": "image-message",
            "open_kfid": "wk-1",
            "external_userid": "wm-user",
            "origin": 3,
            "msgtype": "image",
            "image": {"media_id": "image-media"},
        },
        callback_open_kfid="wk-1",
    )

    async with session_maker() as session:
        media = await session.scalar(select(MediaObject))
        conversation = await session.scalar(select(Conversation))
        assert media is not None
        assert conversation is not None
        assert media.owner_id == conversation.owner_id
        assert media.conversation_id == conversation.id
        assert media.status == "attached"
    await engine.dispose()
