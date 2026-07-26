"""ADR-0012 Phase 4の暗号化SQLite store検証。"""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

import pytest

from securitymasker.aliases.factory import get_or_create_alias
from securitymasker.bootstrap import initialize_layout
from securitymasker.config import load_config
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError, SessionError
from securitymasker.gateway.runtime import GatewayRuntime
from securitymasker.models import ReplacementProfile, RestorePolicy
from securitymasker.sessions.sqlite import SQLiteSessionStore

SECRET = "prod-db01.internal.example"
RAW_SESSION_ID = "raw-session-synthetic-123"
RAW_RESPONSE_ID = "raw-response-synthetic-456"


def _paths(tmp_path: Path, *, mode: str = "chatgpt") -> tuple[Path, Path]:
    initialize_layout(tmp_path, mode=mode, port=4000)
    return (
        tmp_path / "securitymasker.state/securitymasker.db",
        tmp_path / "securitymasker.state/securitymasker.key",
    )


def _mapping(session) -> str:
    return get_or_create_alias(
        session,
        original_value=SECRET,
        fingerprint_value=SECRET,
        entity_type="HOSTNAME",
        replacement_profile=ReplacementProfile.HOSTNAME.value,
        restore_policy=RestorePolicy.LITERAL.value,
    ).alias


@pytest.mark.asyncio
async def test_roundtrip_survives_restart_and_database_contains_no_plaintext(
    tmp_path: Path,
) -> None:
    database, key = _paths(tmp_path)
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    session = await store.get_or_create(RAW_SESSION_ID)
    alias = _mapping(session)
    await store.save(session)
    await store.bind_response(RAW_RESPONSE_ID, RAW_SESSION_ID)
    store.close()

    state_bytes = b"".join(
        path.read_bytes()
        for path in database.parent.iterdir()
        if path.is_file() and path.name != "securitymasker.key"
    )
    assert SECRET.encode() not in state_bytes
    assert RAW_SESSION_ID.encode() not in state_bytes
    assert RAW_RESPONSE_ID.encode() not in state_bytes
    assert session.session_index_key not in state_bytes
    assert session.aead_key not in state_bytes

    reopened = SQLiteSessionStore(database, key, mode="chatgpt")
    loaded = await reopened.get(RAW_SESSION_ID)
    assert loaded is not None
    assert MaskingEngine([]).make_restorer(loaded)(alias) == SECRET
    assert await reopened.resolve_response(RAW_RESPONSE_ID) == RAW_SESSION_ID
    reopened.close()


def test_wrong_key_and_wrong_mode_fail_closed(tmp_path: Path) -> None:
    database, key = _paths(tmp_path)
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    store.close()

    with pytest.raises(SessionError, match="different product mode"):
        SQLiteSessionStore(database, key, mode="claude")

    key.write_bytes(secrets.token_bytes(32))
    key.chmod(0o600)
    with pytest.raises(SessionError, match="do not match"):
        SQLiteSessionStore(database, key, mode="chatgpt")


def test_duplicate_active_writer_is_refused(tmp_path: Path) -> None:
    database, key = _paths(tmp_path)
    first = SQLiteSessionStore(database, key, mode="chatgpt")
    try:
        with pytest.raises(SessionError, match="already owned"):
            SQLiteSessionStore(database, key, mode="chatgpt")
    finally:
        first.close()

    replacement = SQLiteSessionStore(database, key, mode="chatgpt")
    replacement.close()


def test_same_database_with_copied_key_is_still_refused(tmp_path: Path) -> None:
    database, key = _paths(tmp_path)
    copied_key = tmp_path / "copied.key"
    copied_key.write_bytes(key.read_bytes())
    copied_key.chmod(0o600)
    first = SQLiteSessionStore(database, key, mode="chatgpt")
    try:
        with pytest.raises(SessionError, match="already owned"):
            SQLiteSessionStore(database, copied_key, mode="chatgpt")
    finally:
        first.close()


@pytest.mark.asyncio
async def test_tampered_record_is_not_returned(tmp_path: Path) -> None:
    database, key = _paths(tmp_path)
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    await store.create(RAW_SESSION_ID)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE records SET sealed=? WHERE kind='session'", (b"tampered",)
        )

    with pytest.raises(SessionError, match="authentication failed"):
        await store.get(RAW_SESSION_ID)
    store.close()


@pytest.mark.asyncio
async def test_sqlite_write_failure_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, key = _paths(tmp_path)
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    session = await store.get_or_create(RAW_SESSION_ID)

    def disk_failure(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("synthetic disk failure")

    monkeypatch.setattr(store, "_write_record", disk_failure)
    with pytest.raises(SessionError, match="SQLite write failed"):
        await store.save(session)
    store.close()


@pytest.mark.asyncio
async def test_v2_runtime_creates_database_on_first_gateway_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = initialize_layout(tmp_path, mode="claude", port=4001)
    config = load_config(layout.config)
    assert config.state is not None
    assert not config.state.database.exists()
    monkeypatch.setenv("SECURITYMASKER_CONFIG", str(layout.config))
    monkeypatch.delenv("SECURITYMASKER_STORE", raising=False)

    runtime = GatewayRuntime.from_env(engine=MaskingEngine([]), config=config)
    assert isinstance(runtime.store, SQLiteSessionStore)
    assert config.state.database.is_file()
    assert runtime.product_mode == "claude"
    runtime.store.close()


@pytest.mark.asyncio
async def test_cli_mode_override_is_bound_into_new_database_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = initialize_layout(tmp_path, mode="chatgpt", port=4000)
    config = load_config(layout.config)
    assert config.state is not None
    monkeypatch.setenv("SECURITYMASKER_CONFIG", str(layout.config))
    monkeypatch.setenv("SECURITYMASKER_PRODUCT_MODE", "claude")

    runtime = GatewayRuntime.from_env(engine=MaskingEngine([]), config=config)
    assert runtime.product_mode == "claude"
    runtime.store.close()

    with pytest.raises(SessionError, match="different product mode"):
        SQLiteSessionStore(
            config.state.database,
            config.state.key,
            mode="chatgpt",
        )


def test_v2_runtime_refuses_legacy_store_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = initialize_layout(tmp_path, mode="chatgpt", port=4000)
    config = load_config(layout.config)
    monkeypatch.setenv("SECURITYMASKER_CONFIG", str(layout.config))
    monkeypatch.setenv("SECURITYMASKER_STORE", "memory")

    with pytest.raises(ConfigError):
        GatewayRuntime.from_env(engine=MaskingEngine([]), config=config)
