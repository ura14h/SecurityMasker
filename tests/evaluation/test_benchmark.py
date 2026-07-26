"""大幅な性能劣化を検出する軽量な回帰テスト。

Not a precise benchmark (see benchmark.py) — just asserts a 100 KB input masks in
a sane time so accidental quadratic blowups are caught in CI.
"""

from __future__ import annotations

import time

import pytest

from tests.evaluation.benchmark import _engine, _text


@pytest.mark.asyncio
async def test_100kb_masks_within_budget() -> None:
    from securitymasker.sessions.memory import InMemorySessionStore

    engine = _engine(200)
    session = await InMemorySessionStore().get_or_create("s")
    text = _text(100_000)
    start = time.perf_counter()
    await engine.mask_text(session, text)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"100KB masking took {elapsed:.2f}s (possible quadratic blowup)"
