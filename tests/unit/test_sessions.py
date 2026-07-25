"""Session store tests (§30.1: TTL, deletion; §30.4 basics)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from securitymasker.sessions.memory import InMemorySessionStore
from securitymasker.sessions.store import is_expired, new_session


@pytest.mark.asyncio
async def test_get_or_create_is_stable() -> None:
    store = InMemorySessionStore()
    a = await store.get_or_create("s1")
    b = await store.get_or_create("s1")
    assert a is b


@pytest.mark.asyncio
async def test_delete_removes_session() -> None:
    store = InMemorySessionStore()
    await store.get_or_create("s1")
    await store.delete("s1")
    assert await store.get("s1") is None
    assert "s1" not in await store.list_ids()


@pytest.mark.asyncio
async def test_idle_ttl_expiry() -> None:
    store = InMemorySessionStore(idle_ttl=timedelta(0))
    await store.get_or_create("s1")
    await asyncio.sleep(0.001)
    # Zero idle TTL means it is immediately considered expired on next access.
    assert await store.get("s1") is None


def test_absolute_ttl_boundary() -> None:
    s = new_session("s1", absolute_ttl=timedelta(hours=1))
    assert not is_expired(s, idle_ttl=timedelta(hours=4))
    s.expires_at = s.created_at  # force absolute expiry
    assert is_expired(s, idle_ttl=timedelta(hours=4))


@pytest.mark.asyncio
async def test_concurrent_first_registration_single_session() -> None:
    """Parallel get_or_create for a new id yields one shared session (§30.4)."""
    store = InMemorySessionStore()
    results = await asyncio.gather(*[store.get_or_create("s1") for _ in range(20)])
    assert all(r is results[0] for r in results)


@pytest.mark.asyncio
async def test_sessions_do_not_share_keys() -> None:
    store = InMemorySessionStore()
    a = await store.get_or_create("a")
    b = await store.get_or_create("b")
    assert a.session_index_key != b.session_index_key
    assert a.aead_key != b.aead_key


# --- lock lifetime -----------------------------------------------------------------
#
# The per-session lock is what keeps two concurrent turns off the same alias table.
# It has to outlive the session RECORD (which a TTL sweep can remove at any moment)
# while still being reclaimed, or a long-lived gateway accumulates one lock object
# per session it has ever seen.


@pytest.mark.asyncio
async def test_delete_does_not_drop_a_held_lock() -> None:
    store = InMemorySessionStore()
    entered = asyncio.Event()
    second_entered = asyncio.Event()

    async def holder() -> None:
        async with store.lock("s1"):
            entered.set()
            await store.delete("s1")          # session expires/reaped while held
            await asyncio.sleep(0.05)

    async def contender() -> None:
        await entered.wait()
        async with store.lock("s1"):
            second_entered.set()

    task = asyncio.gather(holder(), contender())
    await asyncio.sleep(0.02)
    # The contender must still be waiting: the lock outlives the session record.
    assert not second_entered.is_set(), "two holders entered the same session lock"
    await task


@pytest.mark.asyncio
async def test_locks_are_reclaimed_for_dead_sessions() -> None:
    store = InMemorySessionStore()
    for i in range(200):
        key = f"eph-{i}"
        async with store.lock(key):
            await store.get_or_create(key)
        await store.delete(key)
        async with store.lock(key):   # a later touch must not leak an entry
            pass
    assert len(store._locks) == 0, f"{len(store._locks)} locks leaked"


@pytest.mark.asyncio
async def test_live_session_keeps_its_lock() -> None:
    store = InMemorySessionStore()
    async with store.lock("live"):
        await store.get_or_create("live")
    # Session still alive -> its lock is retained for the next request.
    assert "live" in store._locks


@pytest.mark.asyncio
async def test_waiters_keep_the_lock_entry_alive() -> None:
    store = InMemorySessionStore()
    entered = asyncio.Event()
    second = asyncio.Event()

    async def holder() -> None:
        async with store.lock("k"):
            entered.set()
            await asyncio.sleep(0.05)

    async def waiter() -> None:
        await entered.wait()
        async with store.lock("k"):
            second.set()

    await asyncio.gather(holder(), waiter())
    assert second.is_set()   # the waiter used the SAME lock, no double entry


@pytest.mark.asyncio
async def test_lock_handle_raises_when_lost() -> None:
    """A lock lost mid-turn (Redis expiry, failover) must abort, not continue."""
    from securitymasker.errors import SessionError
    from securitymasker.sessions.store import LockHandle

    lost = asyncio.Event()
    handle = LockHandle(lost)
    handle.check()          # still owned: no raise
    lost.set()
    with pytest.raises(SessionError):
        handle.check()
