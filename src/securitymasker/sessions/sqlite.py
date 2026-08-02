"""単一process用の暗号化SQLite session store。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import os
import secrets
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from securitymasker.errors import CryptoError, SessionError
from securitymasker.models import MaskingSession
from securitymasker.sessions.codec import deserialize_session, serialize_session
from securitymasker.sessions.crypto import decrypt, encrypt
from securitymasker.sessions.store import (
    DEFAULT_ABSOLUTE_TTL,
    DEFAULT_IDLE_TTL,
    LockHandle,
    is_expired,
    new_session,
)

_SCHEMA_VERSION = "1"
_JOURNAL_SIZE_LIMIT = 8 * 1024 * 1024
_ACTIVE_DATABASES: set[Path] = set()
_ACTIVE_DATABASES_LOCK = threading.Lock()


def _windows_require_private(path: Path, *, directory: bool) -> None:
    if os.name != "nt":
        return
    from securitymasker.windows_security import (
        WindowsSecurityError,
        require_local_fixed_ntfs,
        require_private_dacl,
    )

    try:
        require_local_fixed_ntfs(path)
        require_private_dacl(path, directory=directory)
    except WindowsSecurityError as exc:
        raise SessionError(f"Windows SQLite state is not private: {exc}") from None


def _windows_secure(path: Path, *, directory: bool) -> None:
    if os.name != "nt":
        return
    from securitymasker.windows_security import WindowsSecurityError, secure_path

    try:
        secure_path(path, directory=directory)
    except WindowsSecurityError as exc:
        raise SessionError(f"cannot protect Windows SQLite state: {exc}") from None


def _now() -> datetime:
    return datetime.now(UTC)


class SQLiteSessionStore:
    """DB metadata・lookup・recordをmaster keyへ暗号的に拘束するstore。"""

    def __init__(
        self,
        database: str | Path,
        key_file: str | Path,
        *,
        mode: str,
        idle_ttl: timedelta = DEFAULT_IDLE_TTL,
        absolute_ttl: timedelta = DEFAULT_ABSOLUTE_TTL,
    ) -> None:
        if mode not in {"chatgpt", "claude"}:
            raise SessionError("SQLite store mode must be 'chatgpt' or 'claude'")
        self._database = Path(database)
        self._key_file = Path(key_file)
        self._mode = mode
        self._idle_ttl = idle_ttl
        self._absolute_ttl = absolute_ttl
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_refs: dict[str, int] = {}
        self._lease_file: BinaryIO | None = None
        self._writer_lease_path: Path | None = None
        self._database_lease_file: BinaryIO | None = None
        self._database_lease_path = Path(f"{self._database}.lock")
        self._registered_database: Path | None = None
        self._closed = False
        try:
            resolved_database = self._database.resolve()
            with _ACTIVE_DATABASES_LOCK:
                if resolved_database in _ACTIVE_DATABASES:
                    raise SessionError(
                        "SQLite database is already owned by another SecurityMasker process"
                    )
                _ACTIVE_DATABASES.add(resolved_database)
            self._registered_database = resolved_database
            _windows_require_private(self._key_file.parent, directory=True)
            if not self._key_file.exists():
                raise SessionError("SQLite session store initialization failed: FileNotFoundError")
            _windows_require_private(self._key_file, directory=False)
            self._master_key = self._key_file.read_bytes()
            if len(self._master_key) != 32:
                raise SessionError("SQLite master key must contain exactly 32 bytes")
            self._acquire_writer_lease()
            self._acquire_database_lease()
            self._initialize()
        except SessionError:
            self.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            self.close()
            raise SessionError(
                f"SQLite session store initialization failed: {type(exc).__name__}"
            ) from None

    def _acquire_writer_lease(self) -> None:
        lease_path = (
            self._key_file.parent.with_name("securitymasker.state.lock")
            if os.name == "nt"
            else self._key_file
        )
        if os.name == "nt":
            lease_path.touch(exist_ok=True)
            _windows_secure(lease_path, directory=False)
        lease = lease_path.open("a+b" if os.name == "nt" else "rb")
        try:
            if os.name == "posix":
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - Windows native buildで検証する
                msvcrt = importlib.import_module("msvcrt")

                msvcrt.locking(
                    lease.fileno(),
                    msvcrt.LK_NBLCK,
                    1,
                )
        except (OSError, BlockingIOError):
            lease.close()
            raise SessionError(
                "SQLite state is already owned by another SecurityMasker process"
            ) from None
        self._lease_file = lease
        self._writer_lease_path = lease_path if os.name == "nt" else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=0, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(f"PRAGMA journal_size_limit = {_JOURNAL_SIZE_LIMIT}")
            self._secure_database_artifacts()
            return connection
        except BaseException:
            connection.close()
            raise

    def _secure_database_artifacts(self) -> None:
        if os.name != "nt":
            return
        for path in (
            self._database,
            Path(f"{self._database}-wal"),
            Path(f"{self._database}-shm"),
            Path(f"{self._database}-journal"),
        ):
            if path.exists():
                _windows_secure(path, directory=False)

    def _acquire_database_lease(self) -> None:
        # 同じDBへkey fileのcopyを組み合わせた二重起動も拒否する。
        self._database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                self._database_lease_path.touch(exist_ok=True)
                _windows_secure(self._database_lease_path, directory=False)
            lease = self._database_lease_path.open("a+b")
        except OSError:
            raise SessionError(
                "SQLite database is already owned by another SecurityMasker process"
            ) from None
        try:
            if os.name == "posix":
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - Windows native buildで検証する
                msvcrt = importlib.import_module("msvcrt")

                msvcrt.locking(
                    lease.fileno(),
                    msvcrt.LK_NBLCK,
                    1,
                )
        except (OSError, BlockingIOError):
            lease.close()
            raise SessionError(
                "SQLite database is already owned by another SecurityMasker process"
            ) from None
        if os.name == "posix":
            os.chmod(self._database_lease_path, 0o600)
        self._database_lease_file = lease

    def _initialize(self) -> None:
        self._database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = self._connect()
        try:
            self._enable_page_reclamation(connection)
            connection.execute("BEGIN IMMEDIATE")
            self._secure_database_artifacts()
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata "
                "(name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            metadata = dict(connection.execute("SELECT name, value FROM metadata"))
            if not metadata:
                database_id = secrets.token_hex(16)
                values = {
                    "schema_version": _SCHEMA_VERSION,
                    "database_id": database_id,
                    "mode": self._mode,
                    "key_check": self._key_check(database_id),
                }
                connection.executemany(
                    "INSERT INTO metadata(name, value) VALUES (?, ?)", values.items()
                )
                metadata = values
            self._validate_metadata(metadata)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                "kind TEXT NOT NULL, lookup BLOB NOT NULL, sealed BLOB NOT NULL, "
                "expires REAL NOT NULL, PRIMARY KEY(kind, lookup))"
            )
            self._purge_expired(connection)
            connection.execute("COMMIT")
            self._reclaim_pages(connection)
            self._database_id = metadata["database_id"]
            if os.name == "posix":
                os.chmod(self._database, 0o600)
            else:
                self._secure_database_artifacts()
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def _enable_page_reclamation(connection: sqlite3.Connection) -> None:
        """既存DBを含め、削除済みpageを末尾から縮小できる形式にする。"""
        mode = int(connection.execute("PRAGMA auto_vacuum").fetchone()[0])
        if mode != 2:  # 2 = INCREMENTAL
            connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
            # 既存DBはVACUUMでfile formatを書き換えないと設定が有効にならない。
            connection.execute("VACUUM")

    @staticmethod
    def _reclaim_pages(connection: sqlite3.Connection) -> None:
        """freelist pageをDB末尾から回収する。"""
        remaining = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        while remaining > 0:
            connection.execute(f"PRAGMA incremental_vacuum({remaining})")
            current = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
            if current >= remaining:
                break
            remaining = current

    @staticmethod
    def _purge_expired(connection: sqlite3.Connection) -> int:
        """期限切れsessionとresponse bindingを一括削除する。"""
        cursor = connection.execute(
            "DELETE FROM records WHERE expires<=?", (_now().timestamp(),)
        )
        return max(cursor.rowcount, 0)

    def _key_check(self, database_id: str) -> str:
        message = f"securitymasker-key-check\0{database_id}\0{self._mode}".encode()
        return hmac.new(self._master_key, message, hashlib.sha256).hexdigest()

    def _validate_metadata(self, metadata: dict[str, str]) -> None:
        required = {"schema_version", "database_id", "mode", "key_check"}
        if set(metadata) != required:
            raise SessionError("SQLite metadata is incomplete or has unknown fields")
        if metadata["schema_version"] != _SCHEMA_VERSION:
            raise SessionError("SQLite schema version is not supported")
        if metadata["mode"] != self._mode:
            raise SessionError("SQLite database belongs to a different product mode")
        expected = self._key_check(metadata["database_id"])
        if not hmac.compare_digest(metadata["key_check"], expected):
            raise SessionError("SQLite database and master key do not match")

    def _lookup(self, kind: str, raw_id: str) -> bytes:
        message = b"\0".join((kind.encode(), raw_id.encode()))
        return hmac.new(self._master_key, message, hashlib.sha256).digest()

    def _aad(self, kind: str, lookup: bytes) -> bytes:
        return b"\0".join(
            (
                _SCHEMA_VERSION.encode(),
                self._database_id.encode(),
                self._mode.encode(),
                kind.encode(),
                lookup,
            )
        )

    def _read_record(self, kind: str, raw_id: str) -> str | None:
        lookup = self._lookup(kind, raw_id)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT sealed, expires FROM records WHERE kind=? AND lookup=?",
                (kind, lookup),
            ).fetchone()
            if row is None:
                return None
            if float(row[1]) <= _now().timestamp():
                self._delete_record(kind, lookup, connection=connection)
                return None
            try:
                return decrypt(self._master_key, bytes(row[0]), self._aad(kind, lookup))
            except CryptoError:
                raise SessionError(
                    "SQLite record authentication failed (tamper or wrong key)"
                ) from None
        finally:
            connection.close()

    def _delete_record(
        self,
        kind: str,
        lookup: bytes,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owned_connection = connection is None
        active = connection or self._connect()
        try:
            active.execute(
                "DELETE FROM records WHERE kind=? AND lookup=?", (kind, lookup)
            )
            self._reclaim_pages(active)
        finally:
            if owned_connection:
                active.close()

    def _write_record(self, kind: str, raw_id: str, text: str, expires: datetime) -> None:
        lookup = self._lookup(kind, raw_id)
        sealed = encrypt(self._master_key, text, self._aad(kind, lookup))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._purge_expired(connection)
            connection.execute(
                "INSERT OR REPLACE INTO records(kind,lookup,sealed,expires) VALUES(?,?,?,?)",
                (kind, lookup, sealed, expires.timestamp()),
            )
            connection.execute("COMMIT")
            self._reclaim_pages(connection)
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    async def get(self, session_id: str) -> MaskingSession | None:
        try:
            text = self._read_record("session", session_id)
        except sqlite3.Error as exc:
            raise SessionError(f"SQLite read failed: {type(exc).__name__}") from None
        if text is None:
            return None
        session = deserialize_session(text)
        if session.session_id != session_id:
            raise SessionError("SQLite session identifier authentication failed")
        if is_expired(session, self._idle_ttl):
            await self.delete(session_id)
            return None
        return session

    async def create(
        self,
        session_id: str,
        *,
        client_type: str = "unknown",
        lock: LockHandle | None = None,
    ) -> MaskingSession:
        if lock is None:
            async with self.lock(session_id) as held:
                return await self.create(
                    session_id,
                    client_type=client_type,
                    lock=held,
                )
        lock.check()
        session = new_session(
            session_id,
            client_type=client_type,
            absolute_ttl=self._absolute_ttl,
        )
        await self.save(session, lock=lock)
        return session

    async def get_or_create(
        self,
        session_id: str,
        *,
        client_type: str = "unknown",
        lock: LockHandle | None = None,
    ) -> MaskingSession:
        if lock is None:
            async with self.lock(session_id) as held:
                return await self.get_or_create(
                    session_id,
                    client_type=client_type,
                    lock=held,
                )
        lock.check()
        existing = await self.get(session_id)
        return existing or await self.create(
            session_id,
            client_type=client_type,
            lock=lock,
        )

    async def save(
        self, session: MaskingSession, *, lock: LockHandle | None = None
    ) -> None:
        if lock is None:
            async with self.lock(session.session_id) as held:
                await self.save(session, lock=held)
            return
        lock.check()
        session.last_used_at = _now()
        idle_expiry = session.last_used_at + self._idle_ttl
        expires = min(session.expires_at, idle_expiry)
        try:
            self._write_record(
                "session", session.session_id, serialize_session(session), expires
            )
        except sqlite3.Error as exc:
            raise SessionError(f"SQLite write failed: {type(exc).__name__}") from None

    async def delete(
        self,
        session_id: str,
        *,
        lock: LockHandle | None = None,
    ) -> None:
        if lock is None:
            async with self.lock(session_id) as held:
                await self.delete(session_id, lock=held)
            return
        lock.check()
        lookup = self._lookup("session", session_id)
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._purge_expired(connection)
                response_rows = connection.execute(
                    "SELECT lookup,sealed FROM records WHERE kind='response'"
                ).fetchall()
                response_lookups = []
                for response_lookup, sealed in response_rows:
                    response_lookup = bytes(response_lookup)
                    try:
                        bound_session = decrypt(
                            self._master_key,
                            bytes(sealed),
                            self._aad("response", response_lookup),
                        )
                    except CryptoError:
                        raise SessionError(
                            "SQLite record authentication failed (tamper or wrong key)"
                        ) from None
                    if hmac.compare_digest(bound_session, session_id):
                        response_lookups.append(response_lookup)
                connection.execute(
                    "DELETE FROM records WHERE kind='session' AND lookup=?", (lookup,)
                )
                connection.executemany(
                    "DELETE FROM records WHERE kind='response' AND lookup=?",
                    ((item,) for item in response_lookups),
                )
                connection.execute("COMMIT")
                self._reclaim_pages(connection)
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise SessionError(f"SQLite delete failed: {type(exc).__name__}") from None

    async def touch(
        self,
        session_id: str,
        *,
        lock: LockHandle | None = None,
    ) -> None:
        session = await self.get(session_id)
        if session is not None:
            await self.save(session, lock=lock)

    async def list_ids(self) -> list[str]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._purge_expired(connection)
            rows = connection.execute(
                "SELECT lookup,sealed FROM records WHERE kind='session'",
            ).fetchall()
            connection.execute("COMMIT")
            self._reclaim_pages(connection)
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        result = []
        for lookup, sealed in rows:
            try:
                text = decrypt(
                    self._master_key,
                    bytes(sealed),
                    self._aad("session", bytes(lookup)),
                )
                result.append(deserialize_session(text).session_id)
            except (CryptoError, ValueError, KeyError):
                raise SessionError("SQLite session enumeration failed") from None
        return result

    async def bind_response(self, response_id: str, session_key: str) -> None:
        try:
            self._write_record(
                "response",
                response_id,
                session_key,
                _now() + self._idle_ttl,
            )
        except sqlite3.Error as exc:
            raise SessionError(
                f"SQLite response binding failed: {type(exc).__name__}"
            ) from None

    async def resolve_response(self, response_id: str) -> str | None:
        try:
            return self._read_record("response", response_id)
        except sqlite3.Error as exc:
            raise SessionError(
                f"SQLite response lookup failed: {type(exc).__name__}"
            ) from None

    def lock(self, session_id: str) -> AbstractAsyncContextManager[LockHandle]:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        self._lock_refs[session_id] = self._lock_refs.get(session_id, 0) + 1

        @asynccontextmanager
        async def _held() -> AsyncIterator[LockHandle]:
            try:
                async with lock:
                    yield LockHandle()
            finally:
                remaining = self._lock_refs.get(session_id, 1) - 1
                if remaining <= 0:
                    self._lock_refs.pop(session_id, None)
                    self._locks.pop(session_id, None)
                else:
                    self._lock_refs[session_id] = remaining

        return _held()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._lease_file is not None:
            self._lease_file.close()
            self._lease_file = None
            if self._writer_lease_path is not None:
                with suppress(OSError):
                    self._writer_lease_path.unlink()
                self._writer_lease_path = None
        if self._database_lease_file is not None:
            self._database_lease_file.close()
            self._database_lease_file = None
            with suppress(OSError):
                self._database_lease_path.unlink()
        if self._registered_database is not None:
            with _ACTIVE_DATABASES_LOCK:
                _ACTIVE_DATABASES.discard(self._registered_database)
            self._registered_database = None

    def __del__(self) -> None:
        self.close()
