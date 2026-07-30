"""Codex用のOpenAI Responses provider設定を生成するhelper。

``requires_openai_auth = true``によりclient自身のChatGPT OAuth tokenをGatewayへ
透過するため、別のAPI keyは要求しない。Gatewayは認証情報を保存しない。Codexは
``{base_url}/responses``へ送信するため``base_url``に``/v1``を付けない。Responsesの
HTTP/SSEとWebSocketを同じGatewayへ向ける。
"""

from __future__ import annotations

DEFAULT_BASE_URL = "http://127.0.0.1:4000"


def codex_config_toml(base_url: str = DEFAULT_BASE_URL) -> str:
    """SecurityMasker proxy用のCodex ``config.toml`` snippetを返す。

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
supports_websockets = true
"""
