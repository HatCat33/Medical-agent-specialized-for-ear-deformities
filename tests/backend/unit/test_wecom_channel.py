import base64
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from medical_agent.core.config import Settings
from medical_agent.core.database import Base
from medical_agent.modules.channels.common.models import ChannelEventReceipt
from medical_agent.modules.channels.wecom import service as wecom_service
from medical_agent.modules.channels.wecom.client import (
    Frame,
    WeComLongConnectionClient,
    decrypt_media,
    truncate_utf8,
)
from medical_agent.modules.channels.wecom.service import WeComBotRuntime, normalize_message
from medical_agent.modules.conversation import service as conversation_service
from medical_agent.modules.conversation.models import Conversation, Message


def _message_frame(
    *,
    message_id: str,
    message_type: str,
    payload: dict[str, object],
) -> Frame:
    return cast(
        Frame,
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "request-1"},
            "body": {
                "msgid": message_id,
                "aibotid": "bot-1",
                "chattype": "single",
                "from": {"userid": "patient-1"},
                "msgtype": message_type,
                **payload,
            },
        },
    )


def test_normalize_text_voice_and_mixed_messages() -> None:
    text_frame = _message_frame(
        message_id="message-1",
        message_type="text",
        payload={"text": {"content": "  初次就诊需要什么？ "}},
    )
    text = normalize_message(text_frame)
    assert text is not None
    assert text.content == "初次就诊需要什么？"
    assert text.chat_id == "patient-1"
    assert text.input_mode == "text"

    voice_frame = _message_frame(
        message_id="message-2",
        message_type="voice",
        payload={"voice": {"content": "语音问题"}},
    )
    voice = normalize_message(voice_frame)
    assert voice is not None
    assert voice.content == "语音问题"
    assert voice.input_mode == "voice"

    mixed_frame = _message_frame(
        message_id="message-3",
        message_type="mixed",
        payload={
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": "请结合图片说明"}},
                    {
                        "msgtype": "image",
                        "image": {
                            "url": "https://example.test/image",
                            "aeskey": "key",
                        },
                    },
                ]
            }
        },
    )
    mixed = normalize_message(mixed_frame)
    assert mixed is not None
    assert mixed.input_mode == "multimodal"
    assert mixed.content == "请结合图片说明"
    assert len(mixed.images) == 1


def test_decrypt_media_and_utf8_truncation() -> None:
    key = bytes(range(32))
    original = b"\x89PNG\r\n\x1a\nwecom-image"
    padding = 32 - (len(original) % 32)
    padded = original + bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    encoded_key = base64.b64encode(key).decode()
    assert decrypt_media(encrypted, encoded_key) == original
    assert decrypt_media(encrypted, encoded_key.rstrip("=")) == original
    assert len(truncate_utf8("耳" * 10, 10).encode("utf-8")) <= 10


@pytest.mark.asyncio
async def test_wecom_message_uses_stable_conversation_streams_and_deduplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wecom.db'}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    settings = Settings(
        llm_provider="demo",
        rag_enabled=False,
        media_storage_path=str(tmp_path / "media"),
        wecom_bot_enabled=True,
        wecom_bot_id="bot-1",
        wecom_bot_secret="not-a-real-secret",
        wecom_bot_reply_interval_seconds=0.1,
    )
    monkeypatch.setattr(wecom_service, "session_factory", session_maker)
    monkeypatch.setattr(conversation_service, "get_settings", lambda: settings)

    replies: list[tuple[str, bool]] = []

    class FakeClient:
        async def reply_stream(
            self,
            *,
            callback_request_id: str,
            stream_id: str,
            content: str,
            finish: bool,
        ) -> None:
            assert callback_request_id == "request-1"
            assert stream_id
            replies.append((content, finish))

    runtime = WeComBotRuntime()
    runtime._settings = settings  # pyright: ignore[reportPrivateUsage]
    runtime._client = cast(WeComLongConnectionClient, FakeClient())  # pyright: ignore[reportPrivateUsage]
    message = normalize_message(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "request-1"},
            "body": {
                "msgid": "message-1",
                "aibotid": "bot-1",
                "chattype": "single",
                "from": {"userid": "patient-1"},
                "msgtype": "text",
                "text": {"content": "第一次就诊需要准备什么？"},
            },
        }
    )
    assert message is not None

    await runtime._process_message(message)  # pyright: ignore[reportPrivateUsage]
    first_reply_count = len(replies)
    await runtime._process_message(message)  # pyright: ignore[reportPrivateUsage]

    assert replies[0] == ("正在整理相关信息...", False)
    assert replies[-1][1] is True
    assert "Demo 测试模式" in replies[-1][0]
    assert len(replies) == first_reply_count

    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 1
        assert await session.scalar(select(func.count()).select_from(Message)) == 2
        assert await session.scalar(select(func.count()).select_from(ChannelEventReceipt)) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_empty_wecom_voice_transcription_gets_visible_retry_message() -> None:
    replies: list[tuple[str, bool]] = []

    class FakeClient:
        async def reply_stream(
            self,
            *,
            callback_request_id: str,
            stream_id: str,
            content: str,
            finish: bool,
        ) -> None:
            assert callback_request_id == "request-1"
            assert stream_id
            replies.append((content, finish))

    runtime = WeComBotRuntime()
    runtime._client = cast(WeComLongConnectionClient, FakeClient())  # pyright: ignore[reportPrivateUsage]
    await runtime.handle_frame(
        _message_frame(
            message_id="empty-voice",
            message_type="voice",
            payload={"voice": {"content": ""}},
        )
    )

    assert replies == [
        ("没有识别到清晰的语音内容，请靠近麦克风重新说一次，或改用文字输入。", True)
    ]
