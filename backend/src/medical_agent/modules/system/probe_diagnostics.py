"""Actionable probe failures without credentials, raw payloads or patient content."""

import re
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from medical_agent.core.config import Settings


def redact(value: str, settings: Settings) -> str:
    for key in (
        settings.llm_api_key,
        settings.vision_api_key,
        settings.asr_api_key,
        settings.embedding_api_key,
        settings.admin_api_token,
        settings.tts_api_key,
    ):
        if key:
            value = value.replace(key, "[redacted]")
    value = re.sub(r"https?://[^\s<>\"']+", "[service URL]", value)
    value = re.sub(r"(?i)bearer\s+\S+|\bsk-[\w-]+", "[redacted]", value)
    value = re.sub(
        r"(?i)(api[_-]?key|token|authorization|password|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        value,
    )
    return value[:700]


def failure_detail(exc: Exception, kind: str, settings: Settings) -> dict[str, Any]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < 8:
        chain.append(current)
        current = current.__cause__
    response = next(
        (item.response for item in chain if isinstance(item, httpx.HTTPStatusError)), None
    )
    status_code = response.status_code if response is not None else None
    category, summary, advice = (
        "response",
        "模型服务返回的内容无法使用",
        "检查模型是否支持当前接口、流式响应和所选输入类型。",
    )
    code = str(getattr(exc, "detail", str(exc)))
    prefix = {
        "chat": "LLM",
        "vision": "VISION",
        "asr": "ASR",
        "embedding": "EMBEDDING",
        "tts": "TTS",
        "browser_tts": "TTS",
    }[kind]
    missing = [
        f"{prefix}_{name.upper()}"
        for name in ("base_url", "api_key", "model")
        if not getattr(settings, f"{prefix.lower()}_{name}").strip()
    ]
    if code in {"model_not_configured", "tts_not_configured", "tts_voice_required"}:
        if code == "tts_voice_required":
            missing = ["TTS_VOICE / 管理端云端音色 ID"]
        category, summary, advice = (
            "configuration",
            "模型配置不完整",
            "补全 " + "、".join(missing or [f"{prefix}_PROVIDER"]) + "，重启后端后重试。",
        )
    elif status_code is not None:
        category = "http"
        summary, advice = {
            400: (
                "供应商拒绝请求参数",
                "检查模型名称、输入格式及供应商支持的参数；视觉服务需支持图片输入。",
            ),
            401: ("供应商密钥验证失败", f"检查 {prefix}_API_KEY 是否有效、是否已过期。"),
            402: ("供应商账户余额或额度不足", "检查供应商账户余额、套餐及计费状态。"),
            403: ("供应商拒绝访问", "检查密钥权限、模型授权及供应商 IP 白名单。"),
            404: (
                "供应商接口或模型不存在",
                f"核对 {prefix}_BASE_URL 的版本路径和 {prefix}_MODEL。",
            ),
            413: ("测试输入超过供应商限制", "检查供应商的音频、图片或请求大小限制。"),
            422: ("供应商无法解析请求参数", "检查模型接口协议、必填参数与数据格式。"),
            429: ("供应商限流或配额已用完", "检查并发 / 速率限制和账户额度，稍后重试。"),
        }.get(
            status_code,
            (
                "供应商服务异常" if status_code >= 500 else "供应商返回非成功状态",
                "核对供应商状态页与控制台请求日志。",
            ),
        )
    elif any(isinstance(item, (TimeoutError, httpx.TimeoutException)) for item in chain):
        category, summary, advice = (
            "timeout",
            "探活等待超时",
            f"本次最长等待 {settings.tts_timeout_seconds if kind == 'tts' else 16:g} 秒。检查网络和供应商负载后重试。",
        )
    elif any(isinstance(item, httpx.ConnectError) for item in chain):
        category, summary, advice = (
            "connection",
            "无法建立到供应商的连接",
            "检查服务器 DNS、网络代理、TLS 证书和出站网络权限。",
        )
    elif any(isinstance(item, httpx.HTTPError) for item in chain):
        category, summary, advice = (
            "network",
            "供应商连接中断",
            "检查代理、网络稳定性与供应商连接限制。",
        )
    elif code == "model_response_empty":
        summary, advice = (
            "接口成功但没有返回回答文本",
            "模型可能只返回思考内容或输出预算不足。检查思考开关、模型类型及输出限制。",
        )
    elif code.startswith("tts_"):
        summary, advice = {
            "tts_response_invalid": (
                "供应商未返回有效音频",
                "检查模型是否支持 MP3 输出以及接口协议。",
            ),
            "tts_audio_too_large": ("合成音频超过大小限制", "缩短试听文本后重试。"),
            "tts_busy": ("语音合成并发已满", "等待当前语音任务完成后重试。"),
        }.get(code, (summary, advice))

    result: dict[str, Any] = {
        "category": category,
        "summary": summary,
        "suggestion": advice,
        "http_status": status_code,
        "config_prefix": prefix,
        "missing_fields": missing if category == "configuration" else [],
    }
    if response is not None:
        result["request_id"] = redact(
            response.headers.get("x-request-id", response.headers.get("request-id", "")), settings
        )[:120]
        try:
            raw: object = response.json()
            payload = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
            raw_error = payload.get("error", payload)
            if isinstance(raw_error, dict):
                error = cast(dict[str, object], raw_error)
                result["upstream_message"] = redact(
                    str(error.get("message", error.get("detail", ""))), settings
                )
                result["upstream_code"] = redact(str(error.get("code", "")), settings)[:100]
                if kind == "tts" and "voice" in str(error.get("message", "")).lower():
                    result["suggestion"] = (
                        "检查音色 ID 是否属于当前模型。硅基流动预设需使用“模型名:音色名”，"
                        "例如 FunAudioLLM/CosyVoice2-0.5B:anna；克隆音色使用供应商完整 ID。"
                    )
        except (ValueError, httpx.ResponseNotRead):
            pass
    return result


def safe_host(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return "已配置" if value else ""
    return f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")
