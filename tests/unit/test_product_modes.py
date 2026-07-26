"""1 process・1 provider routeのmode分離を検証する。"""

from __future__ import annotations

import httpx
import pytest
from starlette.responses import Response

from securitymasker.bootstrap import initialize_layout
from securitymasker.cli import main
from securitymasker.config import load_config
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError
from securitymasker.gateway import app as gateway_app
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.sessions.memory import InMemorySessionStore


def _runtime(mode: str) -> GatewayRuntime:
    return GatewayRuntime(
        MaskingEngine([]),
        InMemorySessionStore(),
        openai_upstream="http://openai.invalid",
        anthropic_upstream="http://anthropic.invalid",
        product_mode=mode,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "allowed", "refused"),
    [
        ("chatgpt", "/v1/responses", "/v1/messages"),
        ("claude", "/v1/messages", "/v1/responses"),
    ],
)
async def test_mode_exposes_only_its_provider_route(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    allowed: str,
    refused: str,
) -> None:
    forwarded: list[str] = []

    async def fake_streaming(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        processor: object = None,
        on_complete: object = None,
    ) -> Response:
        forwarded.append(url)
        return Response(b"ok")

    async def fake_buffered(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        del method, headers, body
        forwarded.append(url)
        return 200, {"content-type": "application/json"}, b"{}"

    monkeypatch.setattr(gateway_app, "forward_streaming", fake_streaming)
    monkeypatch.setattr(gateway_app, "forward_buffered", fake_buffered)
    app = gateway_app.create_app(_runtime(mode))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        refused_response = await client.post(refused, json={"input": "synthetic"})
        allowed_response = await client.post(allowed, json={"input": "synthetic"})

    assert refused_response.status_code == 404
    assert allowed_response.status_code == 200
    assert len(forwarded) == 1
    if mode == "chatgpt":
        assert forwarded[0] == "http://openai.invalid/responses"
    else:
        assert forwarded[0] == "http://anthropic.invalid/v1/messages"


@pytest.mark.asyncio
async def test_claude_mode_models_use_only_anthropic_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def capture(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        processor: object = None,
        on_complete: object = None,
    ) -> Response:
        calls.append(url)
        return Response(b"ok")

    monkeypatch.setattr(gateway_app, "forward_streaming", capture)
    app = gateway_app.create_app(_runtime("claude"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        supported = await client.get("/v1/models")
        openai_variant = await client.get("/models")

    assert supported.status_code == 200
    assert openai_variant.status_code == 404
    assert calls == ["http://anthropic.invalid/v1/models"]


def test_combined_product_mode_is_rejected() -> None:
    with pytest.raises(ConfigError, match="combined mode"):
        _runtime("both")


def test_runtime_uses_v2_config_mode_and_allows_cli_environment_override(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = initialize_layout(tmp_path, mode="claude", port=4001)
    config = load_config(layout.config)
    monkeypatch.setenv("SECURITYMASKER_CONFIG", str(layout.config))
    monkeypatch.delenv("SECURITYMASKER_PRODUCT_MODE", raising=False)

    configured = GatewayRuntime.from_env(engine=MaskingEngine([]), config=config)
    assert configured.product_mode == "claude"
    configured.store.close()

    override_layout = initialize_layout(
        tmp_path / "override", mode="claude", port=4002
    )
    override_config = load_config(override_layout.config)
    monkeypatch.setenv("SECURITYMASKER_CONFIG", str(override_layout.config))
    monkeypatch.setenv("SECURITYMASKER_PRODUCT_MODE", "chatgpt")
    overridden = GatewayRuntime.from_env(
        engine=MaskingEngine([]), config=override_config
    )
    assert overridden.product_mode == "chatgpt"
    overridden.store.close()


def test_runtime_rejects_combined_mode_from_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = initialize_layout(tmp_path, mode="claude", port=4001)
    config = load_config(layout.config)
    monkeypatch.setenv("SECURITYMASKER_CONFIG", str(layout.config))
    monkeypatch.setenv("SECURITYMASKER_PRODUCT_MODE", "both")

    with pytest.raises(ConfigError, match="must be 'chatgpt' or 'claude'"):
        GatewayRuntime.from_env(engine=MaskingEngine([]), config=config)


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--mode", "chatgpt", "--port", "4555"],
        ["--mode=claude", "--port=4556"],
    ],
)
def test_gateway_command_can_be_omitted(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    observed: list[object] = []

    def fake_gateway(args: object) -> int:
        observed.append(args)
        return 0

    monkeypatch.setattr("securitymasker.cli.cmd_gateway", fake_gateway)
    assert main(arguments) == 0
    assert len(observed) == 1
