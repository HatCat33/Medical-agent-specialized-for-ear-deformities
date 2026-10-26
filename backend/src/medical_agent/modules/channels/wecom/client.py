"""WeCom intelligent bot WebSocket client and encrypted-media helper."""

import asyncio
import base64
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from medical_agent.core.logging import get_logger

logger = get_logger(__name__)

Frame = dict[str, Any]
FrameHandler = Callable[[Frame], Awaitable[None]]
StateHandler = Callable[[str, str | None], None]

DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"
MAX_STREAM_BYTES = 20_480


class WeComProtocolError(RuntimeError):
    pass


def request_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def truncate_utf8(value: str, max_bytes: int = MAX_STREAM_BYTES) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def decrypt_media(encrypted: bytes, aes_key: str) -> bytes:
    normalized_key = aes_key.strip()
    normalized_key += "=" * (-len(normalized_key) % 4)
    try:
        key = base64.b64decode(normalized_key, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise WeComProtocolError("wecom_media_key_invalid") from exc
    if len(key) != 32 or not encrypted:
        raise WeComProtocolError("wecom_media_encryption_invalid")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()
    if not decrypted:
        raise WeComProtocolError("wecom_media_empty")
    padding = decrypted[-1]
    if padding < 1 or padding > 32 or decrypted[-padding:] != bytes([padding]) * padding:
        raise WeComProtocolError("wecom_media_padding_invalid")
    return decrypted[:-padding]


async def download_encrypted_media(
    url: str,
    aes_key: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 15.0,
) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WeComProtocolError("wecom_media_url_invalid")
    encrypted_limit = max_bytes + 64
    content = bytearray()
    try:
        async with (
            httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as http,
            http.stream("GET", url) as response,
        ):
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > encrypted_limit:
                    raise WeComProtocolError("wecom_media_too_large")
    except httpx.HTTPError as exc:
        raise WeComProtocolError("wecom_media_download_failed") from exc
    decrypted = decrypt_media(bytes(content), aes_key)
    if len(decrypted) > max_bytes:
        raise WeComProtocolError("wecom_media_too_large")
    return decrypted


class WeComLongConnectionClient:
    def __init__(
        self,
        *,
        bot_id: str,
        secret: str,
        ws_url: str = DEFAULT_WS_URL,
        heartbeat_seconds: float = 30.0,
        frame_handler: FrameHandler,
        state_handler: StateHandler,
    ) -> None:
        parsed = urlparse(ws_url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise ValueError("wecom_bot_ws_url_invalid")
        self.bot_id = bot_id
        self._secret = secret
        self.ws_url = ws_url.rstrip("/")
        self.heartbeat_seconds = heartbeat_seconds
        self._frame_handler = frame_handler
        self._state_handler = state_handler
        self._connection: ClientConnection | None = None
        self._send_lock = asyncio.Lock()
        self._reply_locks: dict[str, asyncio.Lock] = {}
        self._pending_acks: dict[str, asyncio.Future[Frame]] = {}
        self._handler_tasks: set[asyncio.Future[None]] = set()
        self._stop = asyncio.Event()

    async def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            self._state_handler("connecting", None)
            try:
                async with connect(
                    self.ws_url,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=None,
                    max_size=2 * 1024 * 1024,
                ) as websocket:
                    self._connection = websocket
                    await self._authenticate(websocket)
                    attempt = 0
                    self._state_handler("authenticated", None)
                    await self._receive_loop(websocket)
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, TimeoutError, WeComProtocolError) as exc:
                if self._stop.is_set():
                    break
                attempt += 1
                self._state_handler("reconnecting", str(exc))
                delay = min(30.0, float(2 ** min(attempt - 1, 5)))
                await self._wait_or_stop(delay)
            finally:
                self._connection = None
                self._fail_pending_acks("wecom_connection_lost")
        self._state_handler("stopped", None)

    async def stop(self) -> None:
        self._stop.set()
        connection = self._connection
        if connection is not None:
            await connection.close(code=1000, reason="application_shutdown")
        for task in tuple(self._handler_tasks):
            task.cancel()
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)

    async def reconnect(self) -> None:
        connection = self._connection
        if connection is not None:
            await connection.close(code=1012, reason="server_requested_reconnect")

    async def reply_stream(
        self,
        *,
        callback_request_id: str,
        stream_id: str,
        content: str,
        finish: bool,
    ) -> None:
        body = {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "finish": finish,
                "content": truncate_utf8(content),
            },
        }
        await self._send_with_ack(
            {
                "cmd": "aibot_respond_msg",
                "headers": {"req_id": callback_request_id},
                "body": body,
            },
            callback_request_id,
        )

    async def _authenticate(self, websocket: ClientConnection) -> None:
        req_id = request_id("aibot_subscribe")
        await websocket.send(
            json.dumps(
                {
                    "cmd": "aibot_subscribe",
                    "headers": {"req_id": req_id},
                    "body": {"bot_id": self.bot_id, "secret": self._secret},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        raw = await asyncio.wait_for(websocket.recv(), timeout=10)
        frame = self._decode_frame(raw)
        if frame.get("errcode", 0) != 0:
            raise WeComProtocolError(
                f"wecom_auth_failed:{frame.get('errcode')}:{frame.get('errmsg', 'unknown')}"
            )

    async def _receive_loop(self, websocket: ClientConnection) -> None:
        heartbeat = asyncio.create_task(self._heartbeat_loop(websocket))
        try:
            async for raw in websocket:
                frame = self._decode_frame(raw)
                command = frame.get("cmd")
                if command in {"aibot_msg_callback", "aibot_event_callback"}:
                    task = asyncio.ensure_future(self._frame_handler(frame))
                    self._handler_tasks.add(task)
                    task.add_done_callback(self._handler_done)
                    continue
                raw_headers = frame.get("headers")
                headers = cast(dict[str, object], raw_headers) if isinstance(raw_headers, dict) else {}
                raw_req_id = headers.get("req_id")
                req_id = raw_req_id if isinstance(raw_req_id, str) else None
                pending = self._pending_acks.get(req_id) if req_id else None
                if pending is not None and not pending.done():
                    pending.set_result(frame)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat_loop(self, websocket: ClientConnection) -> None:
        while not self._stop.is_set():
            await self._wait_or_stop(self.heartbeat_seconds)
            if self._stop.is_set():
                return
            await self._send_frame(
                {
                    "cmd": "ping",
                    "headers": {"req_id": request_id("ping")},
                },
                websocket=websocket,
            )

    async def _send_with_ack(self, frame: Frame, req_id: str) -> Frame:
        lock = self._reply_locks.setdefault(req_id, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[Frame] = loop.create_future()
            self._pending_acks[req_id] = future
            try:
                await self._send_frame(frame)
                response = await asyncio.wait_for(future, timeout=10)
            finally:
                self._pending_acks.pop(req_id, None)
            if response.get("errcode", 0) != 0:
                raise WeComProtocolError(
                    f"wecom_reply_failed:{response.get('errcode')}:{response.get('errmsg', 'unknown')}"
                )
            return response

    async def _send_frame(
        self,
        frame: Frame,
        *,
        websocket: ClientConnection | None = None,
    ) -> None:
        connection = websocket or self._connection
        if connection is None:
            raise WeComProtocolError("wecom_not_connected")
        payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        async with self._send_lock:
            await connection.send(payload)

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    def _fail_pending_acks(self, reason: str) -> None:
        for future in self._pending_acks.values():
            if not future.done():
                future.set_exception(WeComProtocolError(reason))
        self._pending_acks.clear()

    def _handler_done(self, future: asyncio.Future[None]) -> None:
        self._handler_tasks.discard(future)
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            logger.error("wecom_callback_handler_failed", error_type=type(error).__name__)

    @staticmethod
    def _decode_frame(raw: str | bytes) -> Frame:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WeComProtocolError("wecom_frame_invalid") from exc
        if not isinstance(value, dict):
            raise WeComProtocolError("wecom_frame_invalid")
        return cast(Frame, value)

