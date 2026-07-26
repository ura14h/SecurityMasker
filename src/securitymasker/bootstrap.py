"""利用者向けlayoutの安全な初期化。"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from securitymasker.errors import ConfigError


@dataclass(frozen=True)
class InitializedLayout:
    root: Path
    config: Path
    dictionary: Path
    state_directory: Path


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except BaseException:
        # fdopen後の例外でもdescriptorはcontext managerが閉じる。
        raise


def initialize_layout(
    directory: str | Path,
    *,
    mode: str,
    port: int,
) -> InitializedLayout:
    """config、辞書、state directory、256-bit keyを一度だけ生成する。

    SQLiteはGatewayの初回起動まで作成しない。既存fileは内容にかかわらず拒否し、
    keyをstdout、error、logへ含めない。
    """
    if mode not in {"chatgpt", "claude"}:
        raise ConfigError("init mode must be 'chatgpt' or 'claude'")
    if not 1 <= port <= 65535:
        raise ConfigError("init port must be between 1 and 65535")

    root = Path(directory).expanduser().resolve()
    config_path = root / "securitymasker.config"
    dictionary_path = root / "securitymasker.dict"
    state_directory = root / "securitymasker.state"
    database_path = state_directory / "securitymasker.db"
    key_path = state_directory / "securitymasker.key"

    for label, path in (
        ("config", config_path),
        ("dictionary", dictionary_path),
        ("state directory", state_directory),
        ("state database", database_path),
        ("master key", key_path),
    ):
        if path.exists():
            raise ConfigError(f"refusing to initialize: {label} already exists")

    package_resources = resources.files("securitymasker.resources")
    config_template = package_resources.joinpath("securitymasker.config").read_text(
        encoding="utf-8"
    )
    dictionary_template = package_resources.joinpath("securitymasker.dict").read_bytes()
    rendered_config = (
        config_template.replace("__MODE__", mode).replace("__PORT__", str(port)).encode("utf-8")
    )
    master_key = secrets.token_bytes(32)

    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_directory.mkdir(mode=0o700)
        os.chmod(state_directory, 0o700)
        _write_private(config_path, rendered_config)
        _write_private(dictionary_path, dictionary_template)
        _write_private(key_path, master_key)
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"initialization failed: {detail}") from None
    finally:
        # immutable bytesは明示消去できない。値を保持する参照だけを直ちに破棄する。
        del master_key

    return InitializedLayout(
        root=root,
        config=config_path,
        dictionary=dictionary_path,
        state_directory=state_directory,
    )
