"""Codex integration helper (§7, §22).

Codex talks to the gateway as a custom OpenAI Responses provider. This module emits
a ready-to-paste config block that maps an environment variable to the
``X-SecurityMasker-Session-ID`` header, and the ``securitymasker run codex`` wrapper
(see ``cli.py``) generates the session id. WebSocket is disabled for now (§22).
"""

from __future__ import annotations

DEFAULT_BASE_URL = "http://127.0.0.1:4000/v1"


def codex_config_toml(
    base_url: str = DEFAULT_BASE_URL, model: str = "securitymasker-openai"
) -> str:
    """Return a Codex ``config.toml`` snippet for the SecurityMasker provider."""
    return f"""\
model = "{model}"
model_provider = "securitymasker"

[model_providers.securitymasker]
name = "SecurityMasker Gateway"
base_url = "{base_url}"
env_key = "LITELLM_API_KEY"
wire_api = "responses"
supports_websockets = false

[model_providers.securitymasker.env_http_headers]
X-SecurityMasker-Session-ID = "SECURITYMASKER_SESSION_ID"
"""
