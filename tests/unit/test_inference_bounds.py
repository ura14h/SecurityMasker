"""推論処理の回数・入力サイズ・時間の上限を検証する。

The audit's finding was that `asyncio.wait_for(asyncio.to_thread(...))` ends the
WAIT but not the work: after a timeout the worker keeps running, and repeated
timeouts exhaust the thread pool. That is a property of CPU-bound Python, not a
bug we can fix — so these tests pin down the containment instead: abandoned work
keeps occupying its slot, and once the limit is reached new work is REFUSED rather
than queued behind stuck workers.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from securitymasker.detectors.inference import (
    BoundedInferenceRunner,
    InferenceOverloaded,
)


@pytest.mark.asyncio
async def test_runs_and_returns_a_result() -> None:
    runner = BoundedInferenceRunner()
    try:
        assert await runner.run(lambda x: x * 2, 21) == 42
    finally:
        runner.shutdown()


@pytest.mark.asyncio
async def test_slot_is_held_until_the_work_finishes_not_until_the_wait_ends() -> None:
    """The core containment property: a timed-out job still occupies its slot."""
    release = threading.Event()
    runner = BoundedInferenceRunner(max_workers=1, max_inflight=2)
    try:
        with pytest.raises(TimeoutError):
            await runner.run(release.wait, timeout=0.05)
        # The caller gave up, but the worker is still running and still counted —
        # this is what stops repeated timeouts from compounding.
        assert runner.inflight == 1
        release.set()
        # Poll for the release: the slot is freed by the pool's completion
        # callback, so there is no awaitable to wait on from here. (ASYNC110's
        # anyio.Event suggestion does not apply — the event we need is internal
        # to concurrent.futures.)
        deadline = time.monotonic() + 2
        while runner.inflight and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        assert runner.inflight == 0      # released once the work actually ended
    finally:
        release.set()
        runner.shutdown()


@pytest.mark.asyncio
async def test_queued_work_is_cancelled_but_running_work_is_not() -> None:
    """Only work that has STARTED is uncancellable; queued work is reclaimed.

    This distinction is the whole reason the limit counts submissions rather than
    running threads: we cannot know which category a job is in from the outside.
    """
    release = threading.Event()
    runner = BoundedInferenceRunner(max_workers=1, max_inflight=4)
    try:
        with pytest.raises(TimeoutError):
            await runner.run(release.wait, timeout=0.05)      # starts running
        with pytest.raises(TimeoutError):
            await runner.run(release.wait, timeout=0.05)      # only ever queued
        # The queued one was cancellable and released its slot; the running one
        # cannot be stopped and still holds hers.
        assert runner.inflight == 1
    finally:
        release.set()
        runner.shutdown()


@pytest.mark.asyncio
async def test_repeated_timeouts_are_refused_rather_than_queued() -> None:
    """Consecutive timeouts must throttle, not accumulate an unbounded backlog."""
    release = threading.Event()
    # Enough workers that each submission actually STARTS, so none is cancellable.
    runner = BoundedInferenceRunner(max_workers=2, max_inflight=2)
    try:
        for _ in range(2):
            with pytest.raises(TimeoutError):
                await runner.run(release.wait, timeout=0.05)
        assert runner.inflight == 2
        # Limit reached: the next request is refused at the door rather than
        # queued behind two workers that cannot be stopped.
        with pytest.raises(InferenceOverloaded):
            await runner.run(release.wait, timeout=0.05)
    finally:
        release.set()
        runner.shutdown()


@pytest.mark.asyncio
async def test_overload_is_a_detection_error_so_the_request_fails_closed() -> None:
    from securitymasker.errors import DetectionError

    release = threading.Event()
    runner = BoundedInferenceRunner(max_workers=1, max_inflight=1)
    try:
        with pytest.raises(TimeoutError):
            await runner.run(release.wait, timeout=0.05)      # starts, holds a slot
        # A DetectionError propagates to the engine, which blocks the request —
        # overload must never degrade into "send it unanalysed".
        with pytest.raises(DetectionError):
            await runner.run(release.wait, timeout=0.05)
    finally:
        release.set()
        runner.shutdown()


@pytest.mark.asyncio
async def test_capacity_is_reclaimed_after_work_completes() -> None:
    runner = BoundedInferenceRunner(max_workers=1, max_inflight=1)
    try:
        assert await runner.run(lambda: "a") == "a"
        assert await runner.run(lambda: "b") == "b"   # slot was returned
        assert runner.inflight == 0
    finally:
        runner.shutdown()


@pytest.mark.asyncio
async def test_thread_pool_size_is_capped() -> None:
    """Runaway inferences must not spawn unbounded threads."""
    release = threading.Event()
    runner = BoundedInferenceRunner(max_workers=2, max_inflight=8,
                                    thread_name_prefix="sm-infer-pooltest")
    try:
        for _ in range(4):
            with pytest.raises(TimeoutError):
                await runner.run(release.wait, timeout=0.02)
        # Count only THIS runner's threads. `sm-infer` is also the prefix used by
        # the process-wide shared runner, so matching on the name alone made the
        # assertion depend on whether an earlier test had warmed that one up.
        live = [t for t in threading.enumerate()
                if t.name.startswith(runner.thread_name_prefix)]
        assert len(live) <= 2, f"{len(live)} inference threads for a 2-worker pool"
    finally:
        release.set()
        runner.shutdown()


@pytest.mark.asyncio
async def test_does_not_use_the_default_executor() -> None:
    """A dedicated pool: stuck inference must not starve the rest of the process."""
    runner = BoundedInferenceRunner(max_workers=1)
    try:
        name = await runner.run(threading.current_thread().__class__ and
                                (lambda: threading.current_thread().name))
        assert name.startswith("sm-infer")
    finally:
        runner.shutdown()


@pytest.mark.asyncio
async def test_exceptions_from_the_work_propagate() -> None:
    runner = BoundedInferenceRunner()
    try:
        def _boom():
            raise ValueError("inference failed")

        with pytest.raises(ValueError):
            await runner.run(_boom)
        assert runner.inflight == 0      # slot released even on failure
    finally:
        runner.shutdown()
