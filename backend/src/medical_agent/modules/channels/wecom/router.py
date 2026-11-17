"""Protected operational status for the WeCom API-mode bot."""

from fastapi import APIRouter, Depends

from medical_agent.api.dependencies import require_admin_token
from medical_agent.modules.channels.wecom.service import runtime

router = APIRouter(
    prefix="/admin/channels/wecom",
    tags=["admin-channels"],
    dependencies=[Depends(require_admin_token)],
)


@router.get("/status")
async def wecom_status() -> dict[str, object]:
    return runtime.status_view()

