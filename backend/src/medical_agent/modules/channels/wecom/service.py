"""WeCom event normalization, conversation dispatch, and bot runtime."""

import asyncio
import hashlib
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from medical_agent.core.config import Settings, get_settings
from medical_agent.core.database import session_factory
from medical_agent.core.logging import get_logger
from medical_agent.modules.channels.common import service as channel_service
from medical_agent.modules.channels.common.contracts import EncryptedRemoteImage, InboundMessage
from medical_agent.modules.channels.wecom.client import (
    Frame,
    WeComLongConnectionClient,
    WeComProtocolError,
    download_encrypted_media,
    request_id,
)
from medical_agent.modules.conversation import service as conversation_service
from medical_agent.modules.media import service as media_service

logger = get_logger(__name__)


@dataclass
class WeComStatus:
    enabled: bool = False
    configured: bool = False
    state: str = "disabled"
    connected: bool = False
    authenticated: bool = False
    last_error: str | None = None
    last_connected_at: datetime | None = None
    last_message_at: datetime | None = None
    reconnect_count: int = 0


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, Any], value)


def normalize_message(frame: Frame) -> InboundMessage | None:
    if frame.get("cmd") != "aibot_msg_callback":
        return None
    headers = _mapping(frame.get("headers"))
    body = _mapping(frame.get("body"))
    sender = _mapping(body.get("from"))
    req_id = str(headers.get("req_id") or "").strip()
    event_id = str(body.get("msgid") or "").strip()
    bot_id = str(body.get("aibotid") or "").strip()
    user_id = str(sender.get("userid") or "").strip()
    chat_type = str(body.get("chattype") or "single")
    if chat_type not in {"single", "group"}:
        return None
    chat_id = str(body.get("chatid") or user_id).strip()
    if not all((req_id, event_id, bot_id, user_id, chat_id)):
        return None

    msg_type = str(body.get("msgtype") or "")
    content = ""
    images: list[EncryptedRemoteImage] = []
    input_mode = "text"
    if msg_type == "text":
        content = str(_mapping(body.get("text")).get("content") or "").strip()
    elif msg_type == "voice":
        content = str(_mapping(body.get("voice")).get("content") or "").strip()
        input_mode = "voice"
    elif msg_type == "image":
        image = _mapping(body.get("image"))
        images.append(
            EncryptedRemoteImage(
                url=str(image.get("url") or ""),
                aes_key=str(image.get("aeskey") or ""),
                filename=f"{event_id}.jpg",
            )
        )
        input_mode = "multimodal"
    elif msg_type == "mixed":
        items = _mapping(body.get("mixed")).get("msg_item")
        raw_items = cast(list[object], items) if isinstance(items, list) else []
        for index, raw_item in enumerate(raw_items):
            item = _mapping(raw_item)
            if item.get("msgtype") == "text":
                text = str(_mapping(item.get("text")).get("content") or "").strip()
                if text:
                    content = f"{content}\n{text}".strip()
            elif item.get("msgtype") == "image":
                image = _mapping(item.get("image"))
                images.append(
                    EncryptedRemoteImage(
                        url=str(image.get("url") or ""),
                        aes_key=str(image.get("aeskey") or ""),
                        filename=f"{event_id}-{index + 1}.jpg",
                    )
                )
        input_mode = "multimodal"
    else:
        return None

    valid_images = tuple(image for image in images if image.url and image.aes_key)
    if not content and not valid_images:
        return None
    return InboundMessage(
        channel="wecom",
        event_id=event_id,
        request_id=req_id,
        bot_id=bot_id,
        user_id=user_id,
        chat_id=chat_id,
        chat_type=cast(Any, chat_type),
        content=content,
        input_mode=cast(Any, input_mode),
        images=valid_images,
    )


class WeComBotRuntime:
    def __init__(self) -> None:
        self.status = WeComStatus()
        self._settings: Settings | None = None
        self._client: WeComLongConnectionClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._chat_locks: dict[str, asyncio.Lock] = {}

    async def start(self, settings: Settings | None = None) -> None:
        if self._task is not None and not self._task.done():
            return
        self._settings = settings or get_settings()
        self.status.enabled = self._settings.wecom_bot_enabled
        self.status.configured = bool(
            self._settings.wecom_bot_id and self._settings.wecom_bot_secret
        )
        if not self.status.enabled:
            self.status.state = "disabled"
            return
        if not self.status.configured:
            self.status.state = "configuration_error"
            self.status.last_error = "wecom_bot_credentials_missing"
            logger.error("wecom_bot_configuration_invalid", reason=self.status.last_error)
            return
        self._client = WeComLongConnectionClient(
            bot_id=self._settings.wecom_bot_id,
            secret=self._settings.wecom_bot_secret,
            ws_url=self._settings.wecom_bot_ws_url,
            heartbeat_seconds=self._settings.wecom_bot_heartbeat_seconds,
            frame_handler=self.handle_frame,
            state_handler=self._handle_state,
        )
        self._task = asyncio.create_task(self._client.run(), name="wecom-bot-runtime")
        self._task.add_done_callback(self._runtime_done)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.stop()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._client = None

    def status_view(self) -> dict[str, object]:
        return asdict(self.status)

    async def handle_frame(self, frame: Frame) -> None:
        body = _mapping(frame.get("body"))
        if frame.get("cmd") == "aibot_event_callback":
            event_type = str(_mapping(body.get("event")).get("eventtype") or "")
            if event_type == "disconnected_event" and self._client is not None:
                await self._client.reconnect()
            return

        message = normalize_message(frame)
        if message is None:
            msg_type = str(body.get("msgtype") or "unknown")
            if msg_type == "voice":
                logger.info("wecom_voice_transcription_empty")
                await self._reply_fixed(
                    frame,
                    "没有识别到清晰的语音内容，请靠近麦克风重新说一次，或改用文字输入。",
                )
            elif msg_type in {"file", "video"}:
                await self._reply_fixed(frame, "暂不支持直接处理文件或视频，请发送文字、语音或图片。")
            else:
                logger.info("wecom_message_ignored", message_type=msg_type)
            return

        self.status.last_message_at = datetime.now(UTC)
        settings = self._require_settings()
        if settings.wecom_bot_allowed_users and message.user_id not in settings.wecom_bot_allowed_users:
            logger.warning("wecom_user_rejected", user_ref=self._user_ref(message.user_id))
            await self._reply_text(message, "当前账号暂未获得使用权限。")
            return
        if message.chat_type == "group" and not settings.wecom_bot_allow_groups:
            await self._reply_text(message, "为保护个人健康信息，请在单聊中使用本助手。")
            return

        lock = self._chat_locks.setdefault(message.chat_id, asyncio.Lock())
        async with lock:
            await self._process_message(message)

    async def _process_message(self, message: InboundMessage) -> None:
        settings = self._require_settings()
        client = self._require_client()
        stream_id = request_id("stream")
        claimed = False
        try:
            async with session_factory() as db:
                claimed = await channel_service.claim_event(
                    db,
                    channel="wecom",
                    event_id=message.event_id,
                )
                if not claimed:
                    logger.info("wecom_message_duplicate", event_id=message.event_id)
                    return
                await client.reply_stream(
                    callback_request_id=message.request_id,
                    stream_id=stream_id,
                    content="正在整理相关信息...",
                    finish=False,
                )
                chat_scope = (
                    f"group:{message.chat_id}"
                    if message.chat_type == "group"
                    else f"single:{message.user_id}"
                )
                conversation = await channel_service.get_or_create_conversation(
                    db,
                    channel="wecom",
                    bot_id=message.bot_id,
                    chat_scope=chat_scope,
                    title="企业微信咨询",
                )
                image_payloads: list[tuple[str, bytes]] = []
                for image in message.images[: settings.media_max_image_count]:
                    data = await download_encrypted_media(
                        image.url,
                        image.aes_key,
                        max_bytes=settings.media_max_image_bytes,
                    )
                    image_payloads.append((image.filename, data))
                media = (
                    await media_service.store_image_bytes(
                        db,
                        images=image_payloads,
                        owner_id=conversation.owner_id,
                        settings=settings,
                    )
                    if image_payloads
                    else []
                )
                cumulative = ""
                last_sent_at = 0.0
                async for event, value in conversation_service.stream_message(
                    db,
                    conversation=conversation,
                    content=message.content,
                    attachment_ids=[item.id for item in media],
                    input_mode=message.input_mode,
                ):
                    if event != "delta" or not isinstance(value, str):
                        continue
                    cumulative += value
                    now = time.monotonic()
                    if now - last_sent_at < settings.wecom_bot_reply_interval_seconds:
                        continue
                    await client.reply_stream(
                        callback_request_id=message.request_id,
                        stream_id=stream_id,
                        content=cumulative,
                        finish=False,
                    )
                    last_sent_at = now
                await client.reply_stream(
                    callback_request_id=message.request_id,
                    stream_id=stream_id,
                    content=cumulative or "服务暂时没有返回有效内容，请稍后重试。",
                    finish=True,
                )
                logger.info(
                    "wecom_message_completed",
                    event_id=message.event_id,
                    conversation_id=conversation.id,
                    user_ref=self._user_ref(message.user_id),
                    input_mode=message.input_mode,
                    image_count=len(media),
                    output_length=len(cumulative),
                )
        except (
            HTTPException,
            OSError,
            RuntimeError,
            SQLAlchemyError,
            ValueError,
            WeComProtocolError,
        ) as exc:
            logger.warning(
                "wecom_message_failed",
                event_id=message.event_id,
                error_type=type(exc).__name__,
                reason=str(exc),
            )
            if claimed:
                async with session_factory() as recovery_db:
                    await channel_service.release_event(
                        recovery_db,
                        channel="wecom",
                        event_id=message.event_id,
                    )
            try:
                await client.reply_stream(
                    callback_request_id=message.request_id,
                    stream_id=stream_id,
                    content="服务暂时无法完成回答，请稍后重试。",
                    finish=True,
                )
            except WeComProtocolError:
                logger.warning("wecom_failure_reply_failed", event_id=message.event_id)

    async def _reply_fixed(self, frame: Frame, content: str) -> None:
        headers = _mapping(frame.get("headers"))
        req_id = str(headers.get("req_id") or "")
        if req_id and self._client is not None:
            await self._client.reply_stream(
                callback_request_id=req_id,
                stream_id=request_id("stream"),
                content=content,
                finish=True,
            )

    async def _reply_text(self, message: InboundMessage, content: str) -> None:
        await self._require_client().reply_stream(
            callback_request_id=message.request_id,
            stream_id=request_id("stream"),
            content=content,
            finish=True,
        )

    def _handle_state(self, state: str, error: str | None) -> None:
        self.status.state = state
        self.status.connected = state in {"authenticated"}
        self.status.authenticated = state == "authenticated"
        if state == "authenticated":
            self.status.last_connected_at = datetime.now(UTC)
            self.status.last_error = None
            logger.info("wecom_bot_authenticated")
        elif state == "reconnecting":
            self.status.reconnect_count += 1
            self.status.last_error = error
            logger.warning("wecom_bot_reconnecting", reason=error)

    def _runtime_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.status.state = "failed"
            self.status.last_error = type(error).__name__
            logger.error("wecom_bot_runtime_failed", error_type=type(error).__name__)

    def _require_settings(self) -> Settings:
        if self._settings is None:
            raise RuntimeError("wecom_runtime_not_started")
        return self._settings

    def _require_client(self) -> WeComLongConnectionClient:
        if self._client is None:
            raise RuntimeError("wecom_client_not_started")
        return self._client

    @staticmethod
    def _user_ref(user_id: str) -> str:
        return hashlib.sha256(user_id.encode()).hexdigest()[:16]


runtime = WeComBotRuntime()

