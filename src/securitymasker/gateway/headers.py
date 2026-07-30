"""provider別の転送header allowlistとleak guard対象を定義する。"""

from __future__ import annotations

from collections.abc import Mapping

# 全upstreamへ転送可能なheader。deny-by-defaultで未記載項目を破棄し、
# custom headerによる機密値の迂回送信を防ぐ。
_COMMON_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "content-type",
        "content-length",
        "user-agent",
    }
)

# provider固有headerを相互に混在させない。``authorization``は両providerで使うため、
# clientが選んだrouteのupstreamへだけ転送する。
_OPENAI_HEADERS = frozenset(
    {
        "authorization",
        "openai-organization",
        "openai-project",
        "openai-beta",
        "chatgpt-account-id",
        "originator",
        "session-id",
        "thread-id",
        "x-openai-internal-codex-responses-lite",
    }
)

_ANTHROPIC_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "anthropic-version",
        "anthropic-beta",
        "anthropic-dangerous-direct-browser-access",
        "x-claude-code-session-id",
    }
)

_PROVIDER_HEADERS: dict[str, frozenset[str]] = {
    "openai": _OPENAI_HEADERS,
    "anthropic": _ANTHROPIC_HEADERS,
}

# auth headerは対応providerへ透過するが、マスク・保存・scan・ログ記録を行わない。
_AUTH_HEADERS = frozenset({"authorization", "x-api-key"})

# user contentではなくclientやproxy transportが生成するheader。
# IPやhostnameを正当に含むため、一般のPII scan対象にはしない。
_TRANSPORT_HEADERS = frozenset(
    {
        "host",
        "connection",
        "upgrade",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
        "content-length",
        "accept-encoding",
        "accept",
        "accept-language",
        "user-agent",
        "via",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)


def is_openai_passthrough(name: str) -> bool:
    """Codexが送る``x-codex-*`` header群をまとめて転送する。"""
    return name.startswith("x-codex-")


def _is_anthropic_passthrough(name: str) -> bool:
    """Anthropicが追加するfeature headerをprotocol namespace単位で透過する。"""
    return name.startswith("anthropic-")


def client_headers(headers: Mapping[str, str], provider: str) -> dict[str, str]:
    """``provider``ごとのallowlist済みheaderを返し、それ以外は破棄する。"""
    allowed = _COMMON_HEADERS | _PROVIDER_HEADERS.get(provider, frozenset())
    return {
        key: value
        for key, value in headers.items()
        if (
            key.lower() in allowed
            or (provider == "openai" and is_openai_passthrough(key.lower()))
            or (provider == "anthropic" and _is_anthropic_passthrough(key.lower()))
        )
    }


def scannable_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """認証・transport headerを除く、leak guard対象のheaderを返す。"""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _AUTH_HEADERS and key.lower() not in _TRANSPORT_HEADERS
    }


def wildcard_headers(headers: Mapping[str, str], provider: str) -> dict[str, str]:
    """自由textを持てるprovider namespace headerだけを返す。"""
    scannable = scannable_headers(headers)
    return {
        key: value
        for key, value in scannable.items()
        if (
            (provider == "openai" and is_openai_passthrough(key.lower()))
            or (provider == "anthropic" and _is_anthropic_passthrough(key.lower()))
        )
    }


def websocket_headers(headers: Mapping[str, str]) -> tuple[dict[str, str], str | None]:
    """OpenAI WebSocket handshakeへ送るheaderとUser-Agentを分離する。"""
    allowed = client_headers(headers, "openai")
    user_agent = allowed.pop("user-agent", allowed.pop("User-Agent", None))
    for name in (
        "accept",
        "accept-encoding",
        "accept-language",
        "content-type",
        "content-length",
    ):
        allowed.pop(name, None)
        allowed.pop(name.title(), None)
    return allowed, user_agent
