"""Short-lived JS-SDK signatures for Enterprise WeChat H5 pages."""

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpx

from medical_agent.core.config import Settings


class WeComH5Error(RuntimeError):
    pass


@dataclass
class CachedValue:
    value: str = ""
    expires_at: float = 0


class WeComH5Signer:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._token = CachedValue()
        self._ticket = CachedValue()
        self._agent_ticket = CachedValue()
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        self._token = CachedValue()
        self._ticket = CachedValue()
        self._agent_ticket = CachedValue()

    async def configuration(self, settings: Settings, page_url: str) -> dict[str, Any]:
        if not settings.wecom_h5_enabled:
            raise WeComH5Error("wecom_h5_disabled")
        if not settings.wecom_corp_id or not settings.wecom_agent_id or not settings.wecom_secret:
            raise WeComH5Error("wecom_h5_credentials_missing")
        normalized_url = self._validate_url(settings, page_url)
        ticket = await self._jsapi_ticket(settings)
        agent_ticket = await self._agent_jsapi_ticket(settings)
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(16)
        return {
            "corp_id": settings.wecom_corp_id,
            "agent_id": settings.wecom_agent_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": self._signature(ticket, nonce, timestamp, normalized_url),
            "agent_signature": self._signature(
                agent_ticket,
                nonce,
                timestamp,
                normalized_url,
            ),
            "url": normalized_url,
        }

    def _validate_url(self, settings: Settings, page_url: str) -> str:
        parsed = urlsplit(page_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise WeComH5Error("wecom_h5_url_invalid")
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        allowed = settings.wecom_h5_allowed_origin_list
        development_local = settings.app_env != "production" and parsed.hostname in {
            "localhost", "127.0.0.1",
        }
        if "*" not in allowed and origin not in allowed and not development_local:
            raise WeComH5Error("wecom_h5_origin_not_allowed")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))

    async def _jsapi_ticket(self, settings: Settings) -> str:
        now = time.monotonic()
        if self._ticket.value and self._ticket.expires_at > now:
            return self._ticket.value
        async with self._lock:
            now = time.monotonic()
            if self._ticket.value and self._ticket.expires_at > now:
                return self._ticket.value
            token = await self._access_token(settings)
            payload = await self._get(
                settings,
                "/cgi-bin/get_jsapi_ticket",
                params={"access_token": token},
            )
            ticket = payload.get("ticket")
            if not isinstance(ticket, str) or not ticket:
                raise WeComH5Error("wecom_h5_ticket_invalid")
            self._ticket = CachedValue(ticket, now + self._ttl(payload))
            return ticket

    async def _access_token(self, settings: Settings) -> str:
        now = time.monotonic()
        if self._token.value and self._token.expires_at > now:
            return self._token.value
        payload = await self._get(
            settings,
            "/cgi-bin/gettoken",
            params={"corpid": settings.wecom_corp_id, "corpsecret": settings.wecom_secret},
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise WeComH5Error("wecom_h5_token_invalid")
        self._token = CachedValue(token, now + self._ttl(payload))
        return token

    async def _agent_jsapi_ticket(self, settings: Settings) -> str:
        now = time.monotonic()
        if self._agent_ticket.value and self._agent_ticket.expires_at > now:
            return self._agent_ticket.value
        async with self._lock:
            now = time.monotonic()
            if self._agent_ticket.value and self._agent_ticket.expires_at > now:
                return self._agent_ticket.value
            token = await self._access_token(settings)
            payload = await self._get(
                settings,
                "/cgi-bin/ticket/get",
                params={"access_token": token, "type": "agent_config"},
            )
            ticket = payload.get("ticket")
            if not isinstance(ticket, str) or not ticket:
                raise WeComH5Error("wecom_h5_agent_ticket_invalid")
            self._agent_ticket = CachedValue(ticket, now + self._ttl(payload))
            return ticket

    async def _get(
        self,
        settings: Settings,
        path: str,
        *,
        params: dict[str, str],
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=settings.wecom_h5_request_timeout_seconds,
            ) as client:
                response = await client.get(
                    settings.wecom_h5_api_base_url.rstrip("/") + path,
                    params=params,
                )
                response.raise_for_status()
                payload = cast(dict[str, Any], response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise WeComH5Error("wecom_h5_upstream_failed") from exc
        if payload.get("errcode") not in (None, 0):
            raise WeComH5Error(f"wecom_h5_api_rejected:{payload.get('errcode')}")
        return payload

    @staticmethod
    def _ttl(payload: dict[str, Any]) -> float:
        expires = payload.get("expires_in", 7200)
        return max(60.0, float(expires) - 300.0)

    @staticmethod
    def _signature(ticket: str, nonce: str, timestamp: int, page_url: str) -> str:
        source = (
            f"jsapi_ticket={ticket}&noncestr={nonce}&timestamp={timestamp}&url={page_url}"
        )
        return hashlib.sha1(source.encode("utf-8")).hexdigest()


signer = WeComH5Signer()
