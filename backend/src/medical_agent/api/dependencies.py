"""Request-scoped authentication, authorization, database, and service dependencies."""

import hashlib
import re
import secrets

from fastapi import Header, HTTPException, Request, status

from medical_agent.core.admin_security import secondary_grant_valid
from medical_agent.core.config import get_settings

_VISITOR_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def owner_reference(owner_id: str) -> str:
    """Return the privacy-preserving owner reference written to interaction logs."""
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:16]


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


async def require_admin_write_access(
    request: Request,
    authorization: str | None = Header(default=None, alias="X-Admin-Authorization"),
) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    settings = get_settings()
    if not settings.admin_secondary_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin_secondary_token_not_configured",
        )
    if not secondary_grant_valid(authorization, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin_secondary_verification_required",
        )

