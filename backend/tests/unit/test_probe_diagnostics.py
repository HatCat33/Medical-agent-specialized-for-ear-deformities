import struct

import httpx
import pytest

from medical_agent.core.config import Settings
from medical_agent.modules.system.model_service import _probe_png  # pyright: ignore[reportPrivateUsage]
from medical_agent.modules.system.probe_diagnostics import failure_detail, safe_host
from medical_agent.providers.llm.client import ProviderError


def test_vision_probe_image_meets_qwen_minimum_dimensions() -> None:
    image = _probe_png()
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", image[16:24])
    assert width > 28 and height > 28


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422, 429, 503])
def test_wrapped_upstream_failure_keeps_status_but_redacts_secrets(status: int) -> None:
    settings = Settings.model_construct(llm_api_key="secret-test-value")
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://vendor.test/v1"),
        headers={"x-request-id": "trace-123"},
        json={
            "error": {
                "code": "denied",
                "message": "Invalid secret-test-value Bearer sk-sensitive https://user:pass@vendor.test/?token=abc",
            }
        },
    )
    error = ProviderError("model_request_failed", status_code=status)
    error.__cause__ = httpx.HTTPStatusError(
        "upstream failure", request=response.request, response=response
    )
    detail = failure_detail(error, "chat", settings)
    assert detail["http_status"] == status
    assert detail["request_id"] == "trace-123"
    assert detail["suggestion"]
    assert "secret-test-value" not in str(detail)
    assert "sk-sensitive" not in str(detail)
    assert "user:pass" not in str(detail)
    assert "token=abc" not in str(detail)


def test_connection_timeout_and_missing_configuration_are_distinguished() -> None:
    settings = Settings.model_construct()
    assert (
        failure_detail(httpx.ConnectError("private host details"), "chat", settings)["category"]
        == "connection"
    )
    error = ProviderError("model_request_failed")
    error.__cause__ = httpx.ReadTimeout("timeout")
    assert failure_detail(error, "chat", settings)["category"] == "timeout"
    missing = failure_detail(RuntimeError("model_not_configured"), "vision", settings)
    assert missing["missing_fields"] == ["VISION_BASE_URL", "VISION_API_KEY", "VISION_MODEL"]
    assert (
        safe_host("https://user:password@vendor.test:443/v1?token=secret")
        == "https://vendor.test:443"
    )
