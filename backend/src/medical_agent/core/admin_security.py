"""Short-lived secondary authorization for sensitive administration requests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime

from medical_agent.core.config import Settings


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signing_key(settings: Settings) -> bytes:
    material = f"{settings.admin_api_token}\0{settings.admin_secondary_token}".encode()
    return hashlib.sha256(material).digest()


def issue_secondary_grant(settings: Settings) -> tuple[str, datetime]:
    expires_at = int(time.time()) + settings.admin_secondary_authorization_ttl_seconds
    payload = _encode(
        json.dumps(
            {"exp": expires_at, "nonce": secrets.token_urlsafe(12)},
            separators=(",", ":"),
        ).encode()
    )
    signature = _encode(hmac.new(_signing_key(settings), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}", datetime.fromtimestamp(expires_at, tz=UTC)


def secondary_grant_valid(value: str | None, settings: Settings) -> bool:
    if not value or not settings.admin_secondary_token:
        return False
    try:
        payload, supplied_signature = value.split(".", 1)
        expected_signature = _encode(
            hmac.new(_signing_key(settings), payload.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False
        decoded = json.loads(_decode(payload))
        return isinstance(decoded, dict) and int(decoded.get("exp", 0)) > int(time.time())
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
