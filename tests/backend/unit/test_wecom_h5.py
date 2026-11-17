"""Enterprise WeChat H5 JS-SDK signing."""

import hashlib
from urllib.parse import parse_qs

import httpx
import pytest

from medical_agent.core.config import Settings
from medical_agent.modules.channels.wecom_h5.service import WeComH5Error, WeComH5Signer


@pytest.mark.asyncio
async def test_wecom_h5_configuration_is_origin_scoped_signed_and_cached() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        query = parse_qs(request.url.query.decode())
        if request.url.path.endswith("/gettoken"):
            assert query["corpid"] == ["corp-test"]
            assert query["corpsecret"] == ["secret-test"]
            return httpx.Response(200, json={"errcode": 0, "access_token": "access-test", "expires_in": 7200})
        assert query["access_token"] == ["access-test"]
        if request.url.path.endswith("/ticket/get"):
            assert query["type"] == ["agent_config"]
            return httpx.Response(200, json={"errcode": 0, "ticket": "agent-ticket-test", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 0, "ticket": "ticket-test", "expires_in": 7200})

    settings = Settings(
        wecom_h5_enabled=True,
        wecom_corp_id="corp-test",
        wecom_agent_id="1000002",
        wecom_secret="secret-test",
        wecom_h5_allowed_origins="https://medical.example.com",
    )
    signer = WeComH5Signer(httpx.MockTransport(handler))
    first = await signer.configuration(
        settings, "https://medical.example.com/chat?from=wecom#ignored"
    )
    source = (
        f"jsapi_ticket=ticket-test&noncestr={first['nonce']}&timestamp={first['timestamp']}"
        "&url=https://medical.example.com/chat?from=wecom"
    )
    assert first["signature"] == hashlib.sha1(source.encode()).hexdigest()
    agent_source = (
        f"jsapi_ticket=agent-ticket-test&noncestr={first['nonce']}&timestamp={first['timestamp']}"
        "&url=https://medical.example.com/chat?from=wecom"
    )
    assert first["agent_signature"] == hashlib.sha1(agent_source.encode()).hexdigest()
    assert first["corp_id"] == "corp-test"
    assert first["agent_id"] == "1000002"
    assert "secret-test" not in str(first)

    await signer.configuration(settings, "https://medical.example.com/another")
    assert requests == [
        "/cgi-bin/gettoken",
        "/cgi-bin/get_jsapi_ticket",
        "/cgi-bin/ticket/get",
    ]

    with pytest.raises(WeComH5Error, match="wecom_h5_origin_not_allowed"):
        await signer.configuration(settings, "https://attacker.example/steal")
