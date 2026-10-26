"""Public callback and protected status routes for WeChat Customer Service."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from medical_agent.api.dependencies import require_admin_token
from medical_agent.core.logging import get_logger
from medical_agent.modules.channels.wechat_kf.crypto import (
    WeChatKfCryptoError,
    encrypted_value,
    parse_callback_event,
)
from medical_agent.modules.channels.wechat_kf.service import runtime

logger = get_logger(__name__)

callback_router = APIRouter(prefix="/channels/wechat-kf", tags=["channel-callbacks"])
admin_router = APIRouter(
    prefix="/admin/channels/wechat-kf",
    tags=["admin-channels"],
    dependencies=[Depends(require_admin_token)],
)

@callback_router.get("/callback")
async def verify_callback(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
) -> Response:
    try:
        plaintext = runtime.callback_crypto().verify_and_decrypt(
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=echostr,
        )
    except (RuntimeError, WeChatKfCryptoError) as exc:
        logger.warning(
            "wechat_kf_callback_verification_failed",
            error_type=type(exc).__name__,
            reason=str(exc),
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wechat_kf_callback_verification_failed") from exc
    return Response(content=plaintext, media_type="text/plain")


@callback_router.post("/callback")
async def receive_callback(
    request: Request,
    msg_signature: str,
    timestamp: str,
    nonce: str,
) -> Response:
    body = await request.body()
    if not body or len(body) > 256 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "wechat_kf_callback_body_invalid")
    try:
        outer_xml = body.decode("utf-8")
        encrypted = encrypted_value(outer_xml)
        plaintext = runtime.callback_crypto().verify_and_decrypt(
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=encrypted,
        )
        event = parse_callback_event(plaintext)
        if event.event != "kf_msg_or_event":
            return Response(content="success", media_type="text/plain")
        if not runtime.validate_corp_id(event.corp_id):
            raise WeChatKfCryptoError("wechat_kf_callback_corp_id_invalid")
        if not runtime.validate_open_kfid(event.open_kfid):
            logger.warning(
                "wechat_kf_open_kfid_not_allowed",
                received_open_kfid=event.open_kfid,
            )
            raise WeChatKfCryptoError("wechat_kf_open_kfid_not_allowed")
        runtime.record_callback()
        runtime.enqueue_sync(open_kfid=event.open_kfid, callback_token=event.token)
    except (UnicodeDecodeError, RuntimeError, WeChatKfCryptoError) as exc:
        logger.warning(
            "wechat_kf_callback_rejected",
            error_type=type(exc).__name__,
            reason=str(exc),
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wechat_kf_callback_rejected") from exc
    return Response(content="success", media_type="text/plain")


@admin_router.get("/status")
async def wechat_kf_status() -> dict[str, object]:
    return runtime.status_view()
