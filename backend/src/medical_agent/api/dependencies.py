"""Request-scoped authentication, authorization, database, and service dependencies."""

import hashlib
import re
import secrets

from fastapi import Header, HTTPException, status

from medical_agent.core.config import get_settings

_VISITOR_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


async def current_owner_id(x_visitor_id: str | None = Header(default=None)) -> str:
    value = (x_visitor_id or "demo-visitor").strip()
    if not _VISITOR_ID.fullmatch(value):
        value = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return value


async def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_api_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin_api_not_configured",
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin_token_invalid",
        )

