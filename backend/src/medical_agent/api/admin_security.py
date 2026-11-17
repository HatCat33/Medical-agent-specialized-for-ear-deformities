"""Secondary verification endpoints for the administration console."""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from medical_agent.api.dependencies import require_admin_token
from medical_agent.core.admin_security import issue_secondary_grant, secondary_grant_valid
from medical_agent.core.config import get_settings

router = APIRouter(
    prefix="/admin/security",
    tags=["admin-security"],
    dependencies=[Depends(require_admin_token)],
)


class SecondaryVerificationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    token: str = Field(min_length=1, max_length=8000)


@router.get("/secondary")
async def secondary_status(
    authorization: str | None = Header(default=None, alias="X-Admin-Authorization"),
) -> dict[str, object]:
    settings = get_settings()
    return {
        "configured": bool(settings.admin_secondary_token),
        "verified": secondary_grant_valid(authorization, settings),
        "ttl_seconds": settings.admin_secondary_authorization_ttl_seconds,
    }


@router.post("/secondary/verify")
async def verify_secondary_token(payload: SecondaryVerificationRequest) -> dict[str, str]:
    settings = get_settings()
    if not settings.admin_secondary_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin_secondary_token_not_configured",
        )
    if not secrets.compare_digest(payload.token, settings.admin_secondary_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin_secondary_token_invalid",
        )
    grant, expires_at = issue_secondary_grant(settings)
    return {"authorization": grant, "expires_at": expires_at.isoformat()}
