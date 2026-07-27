"""v2 configから永続client設定snippetを生成する。"""

from __future__ import annotations

from securitymasker.config import SecurityMaskerConfig
from securitymasker.errors import ConfigError
from securitymasker.integrations.codex import codex_config_toml


def gateway_url(config: SecurityMaskerConfig) -> str:
    """設定されたloopback Gateway URLを返す。"""
    if config.runtime is None:
        raise ConfigError("client configuration requires a version 2 runtime section")
    host = config.runtime.host
    rendered_host = f"[{host}]" if ":" in host else host
    return f"http://{rendered_host}:{config.runtime.port}"


def client_setup_snippet(config: SecurityMaskerConfig) -> str:
    """configのmodeだけに対応する、手動適用用snippetを返す。

    この関数は文字列を生成するだけで、利用者の設定fileや環境を変更しない。
    """
    if config.runtime is None:
        raise ConfigError("client configuration requires a version 2 runtime section")
    base_url = gateway_url(config)
    if config.runtime.mode == "chatgpt":
        return (
            "# Codex CLIまたはCodex appが読むconfig.tomlへ追記してください。\n"
            "# modelはclient側で選択し、このsnippetでは変更しません。\n"
            f"{codex_config_toml(base_url)}"
        )
    environment = client_environment(config)
    return (
        "# Claude Code CLIまたはClaude Code Desktopを起動する環境へ設定してください。\n"
        + "".join(f'export {name}="{value}"\n' for name, value in environment.items())
    )


def client_environment(config: SecurityMaskerConfig) -> dict[str, str]:
    """永続設定を環境変数で受け取るmodeの値を返す。"""
    if config.runtime is None:
        raise ConfigError("client configuration requires a version 2 runtime section")
    if config.runtime.mode == "claude":
        return {"ANTHROPIC_BASE_URL": gateway_url(config)}
    return {}
