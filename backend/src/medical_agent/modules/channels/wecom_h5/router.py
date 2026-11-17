"""Public Enterprise WeChat H5 JS-SDK configuration endpoint."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from medical_agent.core.config import get_settings
from medical_agent.modules.channels.wecom_h5.service import WeComH5Error, signer

router = APIRouter(prefix="/channels/wecom-h5", tags=["wecom-h5"])


@router.get("/js-sdk-config")
async def js_sdk_configuration(url: str = Query(min_length=8, max_length=4000)) -> dict[str, Any]:
    try:
        return await signer.configuration(get_settings(), url)
    except WeComH5Error as exc:
        code = str(exc)
        status = 403 if code in {"wecom_h5_url_invalid", "wecom_h5_origin_not_allowed"} else 503
        raise HTTPException(status, code) from exc
