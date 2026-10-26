"""Minimal asynchronous client for the official WeChat Customer Service API."""

import asyncio
import json
import time
from typing import Any, cast
from urllib.parse import urlparse

import httpx

from medical_agent.core.config import Settings

JsonObject = dict[str, Any]
_TOKEN_ERRORS = {40014, 42001, 42007}


class WeChatKfApiError(RuntimeError):
    def __init__(self, reason: str, *, errcode: int | None = None) -> None:
        super().__init__(reason)
        self.errcode = errcode


def split_utf8(value: str, max_bytes: int = 2048) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in value:
        size = len(character.encode("utf-8"))
        if current and current_bytes + size > max_bytes:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += size
    if current:
        chunks.append("".join(current))
    return chunks or [""]


class WeChatKfClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlparse(settings.wechat_kf_api_base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("wechat_kf_api_base_url_invalid")
        self._base_url = settings.wechat_kf_api_base_url.rstrip("/")
        self._corp_id = settings.wechat_kf_corp_id
        self._secret = settings.wechat_kf_secret
        self._timeout = settings.wechat_kf_request_timeout_seconds
        self._transport = transport
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _get_access_token(self, *, force: bool = False) -> str:
        if not force and self._access_token and time.monotonic() < self._access_token_expires_at:
            return self._access_token
        async with self._token_lock:
            if (
                not force
                and self._access_token
                and time.monotonic() < self._access_token_expires_at
            ):
                return self._access_token
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                ) as http:
                    response = await http.get(
                        f"{self._base_url}/cgi-bin/gettoken",
                        params={"corpid": self._corp_id, "corpsecret": self._secret},
                    )
            except httpx.HTTPError as exc:
                raise WeChatKfApiError("wechat_kf_token_request_failed") from exc
            payload = self._json_payload(response, "token")
            errcode = self._errcode(payload)
            token = payload.get("access_token")
            if errcode != 0 or not isinstance(token, str) or not token:
                raise WeChatKfApiError("wechat_kf_token_rejected", errcode=errcode)
            expires_in = payload.get("expires_in")
            lifetime = int(expires_in) if isinstance(expires_in, int | float) else 7200
            self._access_token = token
            self._access_token_expires_at = time.monotonic() + max(60, lifetime - 300)
            return token

    async def _post(self, endpoint: str, payload: JsonObject) -> JsonObject:
        for attempt in range(2):
            token = await self._get_access_token(force=attempt > 0)
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                ) as http:
                    response = await http.post(
                        f"{self._base_url}{endpoint}",
                        params={"access_token": token},
                        json=payload,
                    )
            except httpx.HTTPError as exc:
                raise WeChatKfApiError("wechat_kf_api_request_failed") from exc
            result = self._json_payload(response, endpoint)
            errcode = self._errcode(result)
            if errcode in _TOKEN_ERRORS and attempt == 0:
                self._access_token = ""
                continue
            if errcode != 0:
                raise WeChatKfApiError("wechat_kf_api_rejected", errcode=errcode)
            return result
        raise WeChatKfApiError("wechat_kf_token_refresh_failed")

    async def sync_messages(
        self,
        *,
        open_kfid: str,
        callback_token: str,
        cursor: str,
    ) -> tuple[str, bool, list[JsonObject]]:
        body: JsonObject = {
            "cursor": cursor,
            "token": callback_token,
            "limit": 1000,
            "voice_format": 0,
            "open_kfid": open_kfid,
        }
        result = await self._post("/cgi-bin/kf/sync_msg", body)
        next_cursor = result.get("next_cursor")
        has_more = result.get("has_more")
        raw_messages = result.get("msg_list")
        if not isinstance(next_cursor, str) or not isinstance(raw_messages, list):
            raise WeChatKfApiError("wechat_kf_sync_response_invalid")
        message_values = cast(list[object], raw_messages)
        messages = [cast(JsonObject, item) for item in message_values if isinstance(item, dict)]
        return next_cursor, has_more == 1, messages

    async def send_text(self, *, open_kfid: str, external_userid: str, content: str) -> None:
        for chunk in split_utf8(content.strip() or "服务暂时没有返回有效内容，请稍后重试。"):
            await self._post(
                "/cgi-bin/kf/send_msg",
                {
                    "touser": external_userid,
                    "open_kfid": open_kfid,
                    "msgtype": "text",
                    "text": {"content": chunk},
                },
            )

    async def download_media(self, media_id: str, *, max_bytes: int) -> bytes:
        for attempt in range(2):
            token = await self._get_access_token(force=attempt > 0)
            data = bytearray()
            content_type = ""
            try:
                async with (
                    httpx.AsyncClient(
                        timeout=self._timeout,
                        transport=self._transport,
                        follow_redirects=True,
                    ) as http,
                    http.stream(
                        "GET",
                        f"{self._base_url}/cgi-bin/media/get",
                        params={"access_token": token, "media_id": media_id},
                    ) as response,
                ):
                    if response.status_code != 200:
                        raise WeChatKfApiError("wechat_kf_media_request_failed")
                    content_type = response.headers.get("content-type", "")
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise WeChatKfApiError("wechat_kf_media_too_large")
            except httpx.HTTPError as exc:
                raise WeChatKfApiError("wechat_kf_media_request_failed") from exc

            if "application/json" in content_type.lower() or data.startswith(b"{"):
                try:
                    error_payload = cast(JsonObject, json.loads(data))
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
                    raise WeChatKfApiError("wechat_kf_media_response_invalid") from exc
                errcode = self._errcode(error_payload)
                if errcode in _TOKEN_ERRORS and attempt == 0:
                    self._access_token = ""
                    continue
                raise WeChatKfApiError("wechat_kf_media_rejected", errcode=errcode)
            if not data:
                raise WeChatKfApiError("wechat_kf_media_empty")
            return bytes(data)
        raise WeChatKfApiError("wechat_kf_token_refresh_failed")

    @staticmethod
    def _json_payload(response: httpx.Response, endpoint: str) -> JsonObject:
        if response.status_code != 200:
            raise WeChatKfApiError(f"wechat_kf_http_error:{endpoint}")
        try:
            raw = cast(object, response.json())
        except (json.JSONDecodeError, ValueError) as exc:
            raise WeChatKfApiError("wechat_kf_api_response_invalid") from exc
        if not isinstance(raw, dict):
            raise WeChatKfApiError("wechat_kf_api_response_invalid")
        return cast(JsonObject, raw)

    @staticmethod
    def _errcode(payload: JsonObject) -> int:
        value = payload.get("errcode", 0)
        return int(value) if isinstance(value, int | float) else -1
