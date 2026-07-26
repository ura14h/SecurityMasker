"""Codex integration helper（§7、§22、ADR-0006）。

Codex points at the SecurityMasker proxy as a custom OpenAI Responses provider.
With ``requires_openai_auth = true`` Codex forwards its own ChatGPT OAuth token to
the proxy (verified in the Phase 6 spike), so no API key is needed — the proxy
passes the credential through to the ChatGPT backend and never stores it (§25).
``base_url`` has no ``/v1`` suffix: Codex posts to ``{base_url}/responses`` and the
proxy serves ``/responses`` directly. WebSocket stays disabled (§22).
"""

from __future__ import annotations

DEFAULT_BASE_URL = "http://127.0.0.1:4000"


def codex_config_toml(base_url: str = DEFAULT_BASE_URL) -> str:
    """SecurityMasker proxy用のChatGPT/Codex ``config.toml`` snippetを返す。

    modelはclient側の選択なので固定しない。永続設定ではclient自身のconversation
    headerとresponse bindingを使い、wrapper専用session環境変数も要求しない。
    """
    return f"""\
model_provider = "securitymasker"

[model_providers.securitymasker]
name = "SecurityMasker Gateway"
base_url = "{base_url}"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
"""
