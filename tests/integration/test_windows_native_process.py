"""Windows nativeの別process lock、終了、復旧を合成dataで検証する。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from securitymasker.bootstrap import initialize_layout
from securitymasker.errors import SessionError
from securitymasker.sessions.sqlite import SQLiteSessionStore

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows native process contract"),
    pytest.mark.skipif(
        os.environ.get("SM_RUN_WINDOWS_NATIVE") != "1",
        reason="set SM_RUN_WINDOWS_NATIVE=1 to run Windows native process tests",
    ),
]

REPO = Path(__file__).resolve().parents[2]
SESSION_ID = "synthetic-windows-process-session"
RESPONSE_ID = "synthetic-windows-process-response"


def _start_probe(database: Path, key: Path) -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            str(REPO / "devtools/windows_store_probe.py"),
            str(database),
            str(key),
        ],
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    ready = process.stdout.readline().strip()
    if ready != "ready":
        _, stderr = process.communicate(timeout=10)
        pytest.fail(f"Windows store probe did not start (exit={process.returncode}): {stderr}")
    return process


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    layout = initialize_layout(tmp_path / "layout", mode="chatgpt", port=45682)
    return (
        layout.root,
        layout.state_directory / "securitymasker.db",
        layout.state_directory / "securitymasker.key",
    )


async def _assert_persisted(store: SQLiteSessionStore) -> None:
    session = await store.get(SESSION_ID)
    assert session is not None
    assert session.client_type == "synthetic-test-client"
    assert await store.resolve_response(RESPONSE_ID) == SESSION_ID


def _assert_reopens(root: Path, database: Path, key: Path) -> None:
    from securitymasker.windows_security import require_private_dacl

    store = SQLiteSessionStore(database, key, mode="chatgpt")
    try:
        asyncio.run(_assert_persisted(store))
        require_private_dacl(database, directory=False)
        require_private_dacl(Path(f"{database}.lock"), directory=False)
        require_private_dacl(root / "securitymasker.state.lock", directory=False)
    finally:
        store.close()


def test_other_process_writer_is_refused_and_graceful_close_recovers(
    tmp_path: Path,
) -> None:
    root, database, key = _paths(tmp_path)
    process = _start_probe(database, key)
    try:
        with pytest.raises(SessionError, match="already owned"):
            SQLiteSessionStore(database, key, mode="chatgpt")
        assert process.stdin is not None
        process.stdin.write("close\n")
        process.stdin.flush()
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)
    _assert_reopens(root, database, key)


def test_forced_termination_leaves_private_artifacts_and_recovers(
    tmp_path: Path,
) -> None:
    from securitymasker.windows_security import require_private_dacl

    root, database, key = _paths(tmp_path)
    process = _start_probe(database, key)
    process.kill()
    process.wait(timeout=15)

    writer_lease = root / "securitymasker.state.lock"
    database_lease = Path(f"{database}.lock")
    assert writer_lease.is_file()
    assert database_lease.is_file()
    require_private_dacl(writer_lease, directory=False)
    require_private_dacl(database_lease, directory=False)
    _assert_reopens(root, database, key)


def test_database_and_key_backup_pair_restores_with_private_dacl(
    tmp_path: Path,
) -> None:
    from securitymasker.windows_security import require_private_dacl, secure_path

    _, database, key = _paths(tmp_path / "original")
    store = SQLiteSessionStore(database, key, mode="chatgpt")
    try:
        asyncio.run(_assert_persisted_after_prepare(store))
    finally:
        store.close()

    backup = tmp_path / "backup"
    backup.mkdir()
    secure_path(backup, directory=True)
    backup_database = backup / database.name
    backup_key = backup / key.name
    shutil.copy2(database, backup_database)
    shutil.copy2(key, backup_key)
    secure_path(backup_database, directory=False)
    secure_path(backup_key, directory=False)
    require_private_dacl(backup, directory=True)
    require_private_dacl(backup_database, directory=False)
    require_private_dacl(backup_key, directory=False)

    restored = initialize_layout(
        tmp_path / "restored" / "layout", mode="chatgpt", port=45683
    )
    restored_database = restored.state_directory / database.name
    restored_key = restored.state_directory / key.name
    shutil.copy2(backup_database, restored_database)
    shutil.copy2(backup_key, restored_key)
    secure_path(restored_database, directory=False)
    secure_path(restored_key, directory=False)
    _assert_reopens(restored.root, restored_database, restored_key)


async def _assert_persisted_after_prepare(store: SQLiteSessionStore) -> None:
    await store.get_or_create(SESSION_ID, client_type="synthetic-test-client")
    await store.bind_response(RESPONSE_ID, SESSION_ID)
    await _assert_persisted(store)
