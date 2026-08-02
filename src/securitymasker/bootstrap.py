"""利用者向けlayoutの安全な初期化。"""

from __future__ import annotations

import importlib
import os
import secrets
import shutil
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import BinaryIO

from securitymasker.errors import ConfigError


@dataclass(frozen=True)
class InitializedLayout:
    root: Path
    config: Path
    dictionary: Path
    state_directory: Path
    replaced_existing: bool = False


_MANAGED_STATE_FILES = frozenset(
    {
        "securitymasker.key",
        "securitymasker.db",
        "securitymasker.db.lock",
        "securitymasker.db-wal",
        "securitymasker.db-shm",
        "securitymasker.db-journal",
    }
)
_MANAGED_ROOT_ENTRIES = frozenset(
    {
        "securitymasker.config",
        "securitymasker.dict",
        "securitymasker.state",
        "securitymasker.state.lock",
    }
)


def default_init_directory(mode: str) -> Path:
    """platform別の利用者data directoryを返す。"""
    if os.name != "nt":
        from securitymasker.config import adjacent_config_directory

        return adjacent_config_directory()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise ConfigError("LOCALAPPDATA is required for Windows initialization")
    return Path(local_app_data) / "SecurityMasker" / mode


def _windows_secure(path: Path, *, directory: bool) -> None:
    if os.name != "nt":
        return
    from securitymasker.windows_security import WindowsSecurityError, secure_path

    try:
        secure_path(path, directory=directory)
    except WindowsSecurityError as exc:
        raise ConfigError(f"cannot protect {path.name}: {exc}") from None


def _posix_uid() -> int:
    """Windowsのtypeshedにも依存せずPOSIX uidを取得する。"""
    return int(vars(os)["getuid"]())


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except BaseException:
        # fdopen後の例外でもdescriptorはcontext managerが閉じる。
        raise
    _windows_secure(path, directory=False)


def _exists(path: Path) -> bool:
    """dangling symlinkも既存targetとして扱う。"""
    return path.exists() or path.is_symlink()


def _require_owned_type(path: Path, *, label: str, directory: bool) -> None:
    """forceで削除できる所有物・file種別だけを許可する。"""
    try:
        metadata = path.lstat()
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"cannot inspect existing {label}: {detail}") from None
    if stat.S_ISLNK(metadata.st_mode):
        raise ConfigError(f"refusing to reset: existing {label} must not be a symlink")
    expected = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not expected:
        kind = "directory" if directory else "regular file"
        raise ConfigError(f"refusing to reset: existing {label} must be a {kind}")
    if os.name == "posix" and metadata.st_uid != _posix_uid():
        raise ConfigError(f"refusing to reset: existing {label} is not owned by current user")
    if os.name == "nt":
        from securitymasker.windows_security import WindowsSecurityError, require_private_dacl

        try:
            require_private_dacl(path, directory=directory)
        except WindowsSecurityError as exc:
            raise ConfigError(
                f"refusing to reset: existing {label} is not private: {exc}"
            ) from None


def _validate_force_targets(
    config_path: Path,
    dictionary_path: Path,
    state_directory: Path,
) -> None:
    """標準layout以外を再帰削除しないため、削除対象を事前確定する。"""
    if _exists(config_path):
        _require_owned_type(config_path, label="config", directory=False)
    if _exists(dictionary_path):
        _require_owned_type(dictionary_path, label="dictionary", directory=False)
    if not _exists(state_directory):
        return
    _require_owned_type(state_directory, label="state directory", directory=True)
    try:
        children = tuple(state_directory.iterdir())
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"cannot inspect existing state directory: {detail}") from None
    for child in children:
        if child.name not in _MANAGED_STATE_FILES:
            raise ConfigError(
                "refusing to reset: state directory contains an unmanaged entry"
            )
        _require_owned_type(child, label=f"state entry {child.name}", directory=False)


def _acquire_key_lease(key_path: Path) -> tuple[BinaryIO, Path | None] | None:
    """稼働中Gatewayのmaster keyを交換しないようnon-blocking lockを取る。"""
    if not _exists(key_path):
        return None
    lease_path = (
        key_path.parent.with_name("securitymasker.state.lock")
        if os.name == "nt"
        else key_path
    )
    try:
        lease = lease_path.open("a+b" if os.name == "nt" else "rb")
        if os.name == "nt":
            _windows_secure(lease_path, directory=False)
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"cannot open existing master key: {detail}") from None
    try:
        if os.name == "posix":
            fcntl = importlib.import_module("fcntl")

            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif os.name == "nt":  # pragma: no cover - Windowsは公開対応範囲外
            msvcrt = importlib.import_module("msvcrt")

            msvcrt.locking(
                lease.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
    except (OSError, BlockingIOError):
        lease.close()
        raise ConfigError(
            "refusing to reset: SecurityMasker state is in use; stop the Gateway first"
        ) from None
    return lease, lease_path if os.name == "nt" else None


def _remove_installed(path: Path) -> None:
    """rollback時に、この処理が生成したtargetだけを除去する。"""
    if not _exists(path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _replace_with_staged_layout(
    *,
    staging: Path,
    backup: Path,
    targets: tuple[Path, ...],
) -> None:
    """旧layoutを退避して新layoutへ切り替え、失敗時は元へ戻す。"""
    moved_old: list[tuple[Path, Path, bool]] = []
    installed: list[Path] = []
    try:
        for target in targets:
            if not _exists(target):
                continue
            saved = backup / target.name
            move_contents = os.name == "nt" and target.is_dir()
            moved_old.append((target, saved, move_contents))
            if move_contents:
                saved.mkdir()
                _windows_secure(saved, directory=True)
                for child in tuple(target.iterdir()):
                    os.replace(child, saved / child.name)
                target.rmdir()
            else:
                os.replace(target, saved)
        for target in targets:
            os.replace(staging / target.name, target)
            installed.append(target)
    except OSError as exc:
        rollback_failed = False
        for target in reversed(installed):
            try:
                _remove_installed(target)
            except OSError:
                rollback_failed = True
        for target, saved, moved_contents in reversed(moved_old):
            try:
                if moved_contents:
                    target.mkdir(exist_ok=True)
                    _windows_secure(target, directory=True)
                    for child in tuple(saved.iterdir()):
                        os.replace(child, target / child.name)
                    saved.rmdir()
                else:
                    os.replace(saved, target)
            except OSError:
                rollback_failed = True
        if rollback_failed:
            raise ConfigError(
                "initialization failed and the previous layout could not be fully restored"
            ) from None
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"initialization failed: {detail}") from None


def initialize_layout(
    directory: str | Path,
    *,
    mode: str,
    port: int,
    force: bool = False,
) -> InitializedLayout:
    """config、辞書、state directory、256-bit keyを生成する。

    SQLiteはGatewayの初回起動まで作成しない。通常は既存fileを拒否する。``force``では
    標準layout全体を一組として置換し、keyをstdout、error、logへ含めない。
    """
    if mode not in {"chatgpt", "claude"}:
        raise ConfigError("init mode must be 'chatgpt' or 'claude'")
    if not 1 <= port <= 65535:
        raise ConfigError("init port must be between 1 and 65535")

    requested_root = Path(directory).expanduser()
    if requested_root.is_symlink():
        raise ConfigError("init directory must not be a symlink")
    root = requested_root.resolve()
    if root.exists() and not root.is_dir():
        raise ConfigError("init directory must be a directory")
    if os.name == "nt":
        from securitymasker.windows_security import (
            WindowsSecurityError,
            require_local_fixed_ntfs,
            require_private_dacl,
        )

        try:
            require_local_fixed_ntfs(requested_root)
            if root.exists():
                entries = {entry.name for entry in root.iterdir()}
                if not entries <= _MANAGED_ROOT_ENTRIES:
                    raise ConfigError(
                        "Windows init directory must not contain unmanaged entries"
                    )
                if entries and force:
                    require_private_dacl(root, directory=True)
        except WindowsSecurityError as exc:
            raise ConfigError(f"Windows init directory is not supported: {exc}") from None
    config_path = root / "securitymasker.config"
    dictionary_path = root / "securitymasker.dict"
    state_directory = root / "securitymasker.state"
    database_path = state_directory / "securitymasker.db"
    key_path = state_directory / "securitymasker.key"

    labeled_targets = (
        ("config", config_path),
        ("dictionary", dictionary_path),
        ("state directory", state_directory),
        ("state database", database_path),
        ("master key", key_path),
    )
    replaced_existing = any(_exists(path) for _, path in labeled_targets)
    if force:
        _validate_force_targets(config_path, dictionary_path, state_directory)
    else:
        for label, path in labeled_targets:
            if _exists(path):
                raise ConfigError(f"refusing to initialize: {label} already exists")

    package_resources = resources.files("securitymasker.resources")
    config_template = package_resources.joinpath("securitymasker.config").read_text(
        encoding="utf-8"
    )
    dictionary_template = package_resources.joinpath("securitymasker.dict").read_bytes()
    rendered_config = (
        config_template.replace("__MODE__", mode).replace("__PORT__", str(port)).encode("utf-8")
    )
    if not force:
        master_key = secrets.token_bytes(32)
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            _windows_secure(root, directory=True)
            state_directory.mkdir(mode=0o700)
            os.chmod(state_directory, 0o700)
            _windows_secure(state_directory, directory=True)
            _write_private(config_path, rendered_config)
            _write_private(dictionary_path, dictionary_template)
            _write_private(key_path, master_key)
        except OSError as exc:
            detail = exc.strerror or exc.__class__.__name__
            raise ConfigError(f"initialization failed: {detail}") from None
        finally:
            # immutable bytesは明示消去できない。保持する参照だけを直ちに破棄する。
            del master_key
        return InitializedLayout(
            root=root,
            config=config_path,
            dictionary=dictionary_path,
            state_directory=state_directory,
        )

    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _windows_secure(root, directory=True)
        with (
            tempfile.TemporaryDirectory(
                prefix=".securitymasker-init-", dir=root.parent
            ) as staging_name,
            tempfile.TemporaryDirectory(
                prefix=".securitymasker-reset-", dir=root.parent
            ) as backup_name,
        ):
            staging = Path(staging_name)
            backup = Path(backup_name)
            _windows_secure(staging, directory=True)
            _windows_secure(backup, directory=True)
            staged_state = staging / "securitymasker.state"
            staged_state.mkdir(mode=0o700)
            os.chmod(staged_state, 0o700)
            _windows_secure(staged_state, directory=True)
            _write_private(staging / "securitymasker.config", rendered_config)
            _write_private(staging / "securitymasker.dict", dictionary_template)
            master_key = secrets.token_bytes(32)
            try:
                _write_private(staged_state / "securitymasker.key", master_key)
            finally:
                # immutable bytesは明示消去できない。保持する参照だけを直ちに破棄する。
                del master_key

            # templateと権限を破壊操作より前に検証する。
            from securitymasker.config import load_config

            load_config(staging / "securitymasker.config")

            lease_record = _acquire_key_lease(key_path) if force else None
            try:
                _replace_with_staged_layout(
                    staging=staging,
                    backup=backup,
                    targets=(config_path, dictionary_path, state_directory),
                )
            finally:
                if lease_record is not None:
                    lease, cleanup_path = lease_record
                    lease.close()
                    if cleanup_path is not None:
                        with suppress(OSError):
                            cleanup_path.unlink()
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        raise ConfigError(f"initialization failed: {detail}") from None

    return InitializedLayout(
        root=root,
        config=config_path,
        dictionary=dictionary_path,
        state_directory=state_directory,
        replaced_existing=replaced_existing,
    )
