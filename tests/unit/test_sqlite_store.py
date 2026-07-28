"""暗号化SQLite session storeの永続化と鍵管理を検証する。"""

from __future__ import annotations

import asyncio
import secrets
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from securitymasker.aliases.factory import get_or_create_alias
from securitymasker.bootstrap import initialize_layout
from securitymasker.config import load_config
from securitymasker.engine import MaskingEngine
from securitymasker.errors import SessionError
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


def test_missing_key_is_not_regenerated_for_existing_database(tmp_path: Path) -> None:
    database, key = _paths(tmp_path)
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    store.close()
    key.unlink()

    with pytest.raises(SessionError, match="FileNotFoundError"):
        SQLiteSessionStore(database, key, mode="chatgpt")
    assert not key.exists()


@pytest.mark.asyncio
async def test_database_and_key_backup_pair_restores_alias_mapping(
    tmp_path: Path,
) -> None:
    database, key = _paths(tmp_path / "original")
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    session = await store.get_or_create(RAW_SESSION_ID)
    alias = _mapping(session)
    await store.save(session)
    store.close()

    restored_state = tmp_path / "restored"
    restored_state.mkdir()
    restored_database = restored_state / "securitymasker.db"
    restored_key = restored_state / "securitymasker.key"
    shutil.copy2(database, restored_database)
    shutil.copy2(key, restored_key)

    restored = SQLiteSessionStore(
        restored_database, restored_key, mode="chatgpt"
    )
    loaded = await restored.get(RAW_SESSION_ID)
    assert loaded is not None
    assert MaskingEngine([]).make_restorer(loaded)(alias) == SECRET
    restored.close()


def test_database_and_key_from_different_backup_sets_are_rejected(
    tmp_path: Path,
) -> None:
    first_database, first_key = _paths(tmp_path / "first")
    second_database, second_key = _paths(tmp_path / "second")
    first = SQLiteSessionStore(first_database, first_key, mode="chatgpt")
    second = SQLiteSessionStore(second_database, second_key, mode="chatgpt")
    first.close()
    second.close()

    with pytest.raises(SessionError, match="do not match"):
        SQLiteSessionStore(first_database, second_key, mode="chatgpt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("idle_ttl", "absolute_ttl"),
    [
        (timedelta(milliseconds=20), timedelta(hours=1)),
        (timedelta(hours=1), timedelta(milliseconds=20)),
    ],
)
async def test_expired_session_stays_expired_after_restart(
    tmp_path: Path,
    idle_ttl: timedelta,
    absolute_ttl: timedelta,
) -> None:
    database, key = _paths(tmp_path)
    store = SQLiteSessionStore(
        database,
        key,
        mode="chatgpt",
        idle_ttl=idle_ttl,
        absolute_ttl=absolute_ttl,
    )
    await store.get_or_create(RAW_SESSION_ID)
    store.close()
    await asyncio.sleep(0.05)

    reopened = SQLiteSessionStore(
        database,
        key,
        mode="chatgpt",
        idle_ttl=idle_ttl,
        absolute_ttl=absolute_ttl,
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 0
    assert await reopened.get(RAW_SESSION_ID) is None
    reopened.close()


@pytest.mark.asyncio
async def test_expired_records_are_pruned_during_normal_writes(tmp_path: Path) -> None:
    database, key = _paths(tmp_path)
    store = SQLiteSessionStore(
        database,
        key,
        mode="chatgpt",
        idle_ttl=timedelta(milliseconds=20),
    )
    for index in range(40):
        session_id = f"synthetic-session-{index}"
        await store.create(session_id)
        await store.bind_response(f"synthetic-response-{index}", session_id)
    await asyncio.sleep(0.05)

    await store.create("live-session")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 1
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
    store.close()


@pytest.mark.asyncio
async def test_deleting_session_also_deletes_its_response_bindings(tmp_path: Path) -> None:
    database, key = _paths(tmp_path)
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    await store.create(RAW_SESSION_ID)
    await store.bind_response("synthetic-response", RAW_SESSION_ID)

    await store.delete(RAW_SESSION_ID)

    assert await store.get(RAW_SESSION_ID) is None
    assert await store.resolve_response("synthetic-response") is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 0
    store.close()


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
async def test_runtime_creates_database_on_first_gateway_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = initialize_layout(tmp_path, mode="claude", port=4001)
    config = load_config(layout.config)
    assert config.state is not None
    assert not config.state.database.exists()
    monkeypatch.setenv("SECURITYMASKER_CONFIG", str(layout.config))
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
