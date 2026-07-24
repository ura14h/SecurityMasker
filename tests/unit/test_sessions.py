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
