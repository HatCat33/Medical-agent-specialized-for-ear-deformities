"""WeChat Customer Service callback synchronization and conversation dispatch."""

import asyncio
import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import cast

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.core.config import Settings, get_settings
from medical_agent.core.database import session_factory
from medical_agent.core.logging import get_logger
from medical_agent.modules.channels.common import service as channel_service
from medical_agent.modules.channels.wechat_kf.client import (
    JsonObject,
    WeChatKfApiError,
    WeChatKfClient,
)
from medical_agent.modules.channels.wechat_kf.crypto import WeChatKfCallbackCrypto
from medical_agent.modules.conversation import service as conversation_service
from medical_agent.modules.media import service as media_service
from medical_agent.modules.speech.service import transcribe_audio_bytes

logger = get_logger(__name__)


@dataclass
class WeChatKfStatus:
    enabled: bool = False
    configured: bool = False
    state: str = "disabled"
    configured_open_kfid_count: int = 0
    pending_tasks: int = 0
    last_callback_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_message_at: datetime | None = None
    last_send_at: datetime | None = None
    last_error: str | None = None


def _mapping(value: object) -> JsonObject:
    return cast(JsonObject, value) if isinstance(value, dict) else {}


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    try:
        return int(value) if isinstance(value, str) else -1
    except ValueError:
        return -1


class WeChatKfRuntime:
    def __init__(self) -> None:
        self.status = WeChatKfStatus()
        self._settings: Settings | None = None
        self._client: WeChatKfClient | None = None
        self._crypto: WeChatKfCallbackCrypto | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._sync_locks: dict[str, asyncio.Lock] = {}

    async def start(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.status.enabled = self._settings.wechat_kf_enabled
        self.status.configured_open_kfid_count = len(self._settings.wechat_kf_open_ids)
        callback_configured = all(
            (
                self._settings.wechat_kf_corp_id,
                self._settings.wechat_kf_secret,
                self._settings.wechat_kf_callback_token,
                self._settings.wechat_kf_callback_aes_key,
            )
        )
        self.status.configured = callback_configured and bool(self._settings.wechat_kf_open_ids)
        if not self.status.enabled:
            self.status.state = "disabled"
            return
        if not callback_configured:
            self.status.state = "configuration_error"
            self.status.last_error = "wechat_kf_configuration_incomplete"
            logger.error("wechat_kf_configuration_invalid", reason=self.status.last_error)
            return
        try:
            self._client = WeChatKfClient(self._settings)
            self._crypto = WeChatKfCallbackCrypto(
                token=self._settings.wechat_kf_callback_token,
                encoding_aes_key=self._settings.wechat_kf_callback_aes_key,
                corp_id=self._settings.wechat_kf_corp_id,
            )
        except (ValueError, RuntimeError) as exc:
            self.status.state = "configuration_error"
            self.status.last_error = str(exc)
            logger.error("wechat_kf_configuration_invalid", reason=str(exc))
            return
        if not self._settings.wechat_kf_open_ids:
            # URL verification only needs callback crypto. Message synchronization
            # remains disabled until an allowed OpenKfId is configured and restarted.
            self.status.state = "waiting_for_open_kfid"
            self.status.last_error = "wechat_kf_open_kfid_missing"
        else:
            self.status.state = "waiting_for_callback"
            self.status.last_error = None

    async def stop(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.status.pending_tasks = 0
        self._client = None
        self._crypto = None

    def status_view(self) -> dict[str, object]:
        self.status.pending_tasks = len(self._tasks)
        return asdict(self.status)

    def callback_crypto(self) -> WeChatKfCallbackCrypto:
        if self._crypto is None:
            raise RuntimeError("wechat_kf_runtime_not_ready")
        return self._crypto

    def validate_open_kfid(self, open_kfid: str) -> bool:
        settings = self._require_settings()
        return open_kfid in settings.wechat_kf_open_ids

    def validate_corp_id(self, corp_id: str) -> bool:
        return corp_id == self._require_settings().wechat_kf_corp_id

    def record_callback(self) -> None:
        self.status.last_callback_at = datetime.now(UTC)

    def enqueue_sync(self, *, open_kfid: str, callback_token: str) -> None:
        if self._client is None or not self.status.enabled or not self.status.configured:
            raise RuntimeError("wechat_kf_runtime_not_ready")
        task = asyncio.create_task(
            self._sync_guarded(open_kfid=open_kfid, callback_token=callback_token),
            name=f"wechat-kf-sync-{hashlib.sha256(open_kfid.encode()).hexdigest()[:8]}",
        )
        self._tasks.add(task)
        self.status.pending_tasks = len(self._tasks)
        task.add_done_callback(self._task_done)

    async def _sync_guarded(self, *, open_kfid: str, callback_token: str) -> None:
        lock = self._sync_locks.setdefault(open_kfid, asyncio.Lock())
        async with lock:
            try:
                await self._sync(open_kfid=open_kfid, callback_token=callback_token)
                self.status.state = "ready"
                self.status.last_error = None
            except asyncio.CancelledError:
                raise
            except (HTTPException, OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
                reason = self._error_reason(exc)
                self.status.state = "degraded"
                self.status.last_error = reason
                logger.warning(
                    "wechat_kf_sync_failed",
                    open_kfid_ref=self._reference(open_kfid),
                    error_type=type(exc).__name__,
                    reason=reason,
                )

    async def _sync(self, *, open_kfid: str, callback_token: str) -> None:
        client = self._require_client()
        async with session_factory() as db:
            cursor = await channel_service.get_sync_cursor(
                db,
                channel="wechat_kf",
                scope=open_kfid,
            )

        for _ in range(100):
            next_cursor, has_more, messages = await client.sync_messages(
                open_kfid=open_kfid,
                callback_token=callback_token,
                cursor=cursor,
            )
            self.status.last_sync_at = datetime.now(UTC)
            for message in messages:
                if _integer(message.get("origin")) != 3:
                    continue
                await self._process_message(message, callback_open_kfid=open_kfid)

            async with session_factory() as db:
                await channel_service.save_sync_cursor(
                    db,
                    channel="wechat_kf",
                    scope=open_kfid,
                    cursor=next_cursor,
                )
            if not has_more:
                return
            if next_cursor == cursor:
                raise WeChatKfApiError("wechat_kf_sync_cursor_stalled")
            cursor = next_cursor
        raise WeChatKfApiError("wechat_kf_sync_page_limit_exceeded")

    async def _process_message(
        self,
        payload: JsonObject,
        *,
        callback_open_kfid: str,
    ) -> None:
        event_id = _string(payload.get("msgid"))
        open_kfid = _string(payload.get("open_kfid"))
        external_userid = _string(payload.get("external_userid"))
        message_type = _string(payload.get("msgtype"))
        if not event_id or not external_userid or open_kfid != callback_open_kfid:
            logger.warning("wechat_kf_message_invalid", message_type=message_type or "unknown")
            return

        self.status.last_message_at = datetime.now(UTC)
        async with session_factory() as db:
            claimed = await channel_service.claim_event(
                db,
                channel="wechat_kf",
                event_id=event_id,
            )
            if not claimed:
                logger.info("wechat_kf_message_duplicate", event_id=event_id)
                return

            settings = self._require_settings()
            if (
                settings.wechat_kf_allowed_external_users
                and external_userid not in settings.wechat_kf_allowed_external_users
            ):
                logger.warning(
                    "wechat_kf_user_rejected",
                    user_ref=self._reference(external_userid),
                )
                await self._send_text(
                    open_kfid=open_kfid,
                    external_userid=external_userid,
                    content="当前微信账号暂未获得使用权限。",
                )
                return

            if message_type in {"file", "video"}:
                await self._send_text(
                    open_kfid=open_kfid,
                    external_userid=external_userid,
                    content="暂不支持直接处理文件或视频，请发送文字、语音或图片。",
                )
                return
            if message_type not in {"text", "voice", "image"}:
                logger.info("wechat_kf_message_ignored", message_type=message_type or "unknown")
                return

            conversation = await channel_service.get_or_create_conversation(
                db,
                channel="wechat_kf",
                bot_id=open_kfid,
                chat_scope=f"single:{external_userid}",
                title="微信客服咨询",
            )
            content = ""
            input_mode = "text"
            attachment_ids: list[str] = []
            try:
                if message_type == "text":
                    content = _string(_mapping(payload.get("text")).get("content"))
                    if not content:
                        await self._send_text(
                            open_kfid=open_kfid,
                            external_userid=external_userid,
                            content="没有收到有效文字内容，请重新发送。",
                        )
                        return
                elif message_type == "voice":
                    input_mode = "voice"
                    media_id = _string(_mapping(payload.get("voice")).get("media_id"))
                    content = await self._transcribe_voice(media_id, settings=settings)
                else:
                    input_mode = "multimodal"
                    media_id = _string(_mapping(payload.get("image")).get("media_id"))
                    attachment_ids = await self._store_image(
                        db,
                        media_id=media_id,
                        owner_id=conversation.owner_id,
                        event_id=event_id,
                        settings=settings,
                    )

                _, assistant_message = await conversation_service.send_message(
                    db,
                    conversation=conversation,
                    content=content,
                    attachment_ids=attachment_ids,
                    input_mode=input_mode,
                    transport="wechat_kf",
                )
            except (HTTPException, WeChatKfApiError) as exc:
                detail = self._error_reason(exc)
                logger.warning(
                    "wechat_kf_input_processing_failed",
                    event_id=event_id,
                    input_mode=input_mode,
                    reason=detail,
                )
                retry_text = (
                    "语音识别暂时不可用，请稍后重试或改用文字输入。"
                    if input_mode == "voice"
                    else "图片暂时无法处理，请重新发送清晰的 JPG、PNG 或 WebP 图片。"
                )
                await self._send_text(
                    open_kfid=open_kfid,
                    external_userid=external_userid,
                    content=retry_text,
                )
                return

            await self._send_text(
                open_kfid=open_kfid,
                external_userid=external_userid,
                content=assistant_message.content,
            )
            logger.info(
                "wechat_kf_message_completed",
                event_id=event_id,
                conversation_id=conversation.id,
                user_ref=self._reference(external_userid),
                input_mode=input_mode,
                image_count=len(attachment_ids),
                output_length=len(assistant_message.content),
            )

    async def _transcribe_voice(self, media_id: str, *, settings: Settings) -> str:
        if not media_id:
            raise HTTPException(400, "wechat_kf_voice_media_missing")
        data = await self._require_client().download_media(
            media_id,
            max_bytes=settings.asr_max_audio_bytes,
        )
        return await transcribe_audio_bytes(
            data,
            filename="wechat-kf-voice.amr",
            media_type="audio/amr",
            settings=settings,
        )

    async def _store_image(
        self,
        db: AsyncSession,
        *,
        media_id: str,
        owner_id: str,
        event_id: str,
        settings: Settings,
    ) -> list[str]:
        if not media_id:
            raise HTTPException(400, "wechat_kf_image_media_missing")
        data = await self._require_client().download_media(
            media_id,
            max_bytes=settings.media_max_image_bytes,
        )
        images = await media_service.store_image_bytes(
            db,
            images=[(f"wechat-kf-{event_id}.jpg", data)],
            owner_id=owner_id,
            settings=settings,
        )
        return [item.id for item in images]

    async def _send_text(self, *, open_kfid: str, external_userid: str, content: str) -> None:
        await self._require_client().send_text(
            open_kfid=open_kfid,
            external_userid=external_userid,
            content=content,
        )
        self.status.last_send_at = datetime.now(UTC)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self.status.pending_tasks = len(self._tasks)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            reason = self._error_reason(error)
            self.status.state = "degraded"
            self.status.last_error = reason
            logger.error("wechat_kf_background_task_failed", reason=reason)

    def _require_settings(self) -> Settings:
        if self._settings is None:
            raise RuntimeError("wechat_kf_runtime_not_started")
        return self._settings

    def _require_client(self) -> WeChatKfClient:
        if self._client is None:
            raise RuntimeError("wechat_kf_client_not_started")
        return self._client

    @staticmethod
    def _reference(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    @staticmethod
    def _error_reason(error: BaseException) -> str:
        if isinstance(error, WeChatKfApiError):
            suffix = f":{error.errcode}" if error.errcode is not None else ""
            return f"{error}{suffix}"
        if isinstance(error, HTTPException):
            return str(error.detail)
        return type(error).__name__


runtime = WeChatKfRuntime()
