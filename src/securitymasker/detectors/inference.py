"""Bounded execution for synchronous model inference (ADR-0011).

The honest constraint first: **a timeout cannot stop CPU-bound Python inference.**
``asyncio.wait_for`` cancels the *await*, not the worker; ``concurrent.futures``
cannot interrupt a running call either. Anything claiming the timeout "bounds
inference" is wrong — it bounds how long the REQUEST waits, and the worker keeps
burning a core until it finishes on its own.

That is survivable only if abandoned work cannot pile up, which is what this
module enforces:

- a **dedicated, fixed-size** thread pool, so runaway inferences occupy at most
  ``max_workers`` threads and can never starve the default executor that the rest
  of the process (and asyncio itself) relies on;
- an **admission limit** on queued-and-running jobs together, so a burst of
  requests is rejected at the door rather than queued into an ever-growing backlog
  behind stuck workers;
- **overload rejection**, not silent queueing: over the limit we raise, the caller
  fails closed, and nothing is sent upstream unanalysed.

The result is bounded resident work, at the cost of refusing requests while the
pool is saturated. That is the correct trade for a masking proxy: refusing is
visible and safe, whereas queueing indefinitely looks healthy right up until it
is not.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from securitymasker.errors import DetectionError

T = TypeVar("T")

# Concurrent inferences. Small on purpose: these are CPU-bound, so more threads
# than cores buys nothing and multiplies the damage a stuck one can do.
DEFAULT_MAX_WORKERS = 2
# Queued + running jobs admitted at once. Past this we reject rather than queue.
DEFAULT_MAX_INFLIGHT = 8


class InferenceOverloaded(DetectionError):
    """Too many inferences in flight; the request is refused (fail-closed)."""


class BoundedInferenceRunner:
    """Runs blocking callables on a fixed pool with an admission limit."""

    def __init__(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        max_inflight: int = DEFAULT_MAX_INFLIGHT,
        thread_name_prefix: str = "sm-infer",
    ) -> None:
        self._max_workers = max_workers
        self._max_inflight = max_inflight
        self._prefix = thread_name_prefix
        # Exposed so a test can count exactly ITS OWN threads: the shared
        # process-wide runner uses the same default prefix.
        self.thread_name_prefix = thread_name_prefix
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        # Counts jobs SUBMITTED and not yet finished — including ones whose caller
        # already timed out and walked away. That is the number that matters: it is
        # the abandoned work we must not let accumulate.
        self._inflight = 0

    @property
    def inflight(self) -> int:
        return self._inflight

    def _pool(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers, thread_name_prefix=self._prefix
                )
            return self._executor

    # ASYNC109: ruff suggests an outer `asyncio.timeout()` instead of a timeout
    # parameter. That does not apply here — the value is the caller's WAIT budget,
    # not a cancellation scope, because the worker thread cannot be cancelled at
    # all. Expressing it as a cancellation scope would restate the exact untruth
    # this module exists to correct.
    async def run(
        self, func: Any, *args: Any, timeout: float | None = None  # noqa: ASYNC109
    ) -> Any:
        """Run ``func(*args)`` on the pool, waiting at most ``timeout`` seconds.

        Raises ``InferenceOverloaded`` if admission is refused, or ``TimeoutError``
        if the wait elapses. In the timeout case the worker keeps running — it
        cannot be stopped — but it still counts against ``max_inflight`` until it
        finishes, so repeated timeouts throttle rather than compound.
        """
        with self._lock:
            if self._inflight >= self._max_inflight:
                raise InferenceOverloaded(
                    f"{self._inflight} inferences already in flight (limit "
                    f"{self._max_inflight}); refusing rather than queueing"
                )
            self._inflight += 1

        loop = asyncio.get_running_loop()
        future = self._pool().submit(func, *args)

        def _release(_: Any) -> None:
            with self._lock:
                self._inflight -= 1

        # Decrement when the WORK finishes, not when the await returns: an
        # abandoned job must keep occupying a slot until it actually completes.
        future.add_done_callback(_release)

        try:
            return await asyncio.wait_for(asyncio.wrap_future(future, loop=loop),
                                          timeout=timeout)
        except TimeoutError:
            # Deliberately NOT cancelling: a running thread cannot be cancelled,
            # and pretending otherwise is what made the previous version wrong.
            raise

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


# One shared runner: a per-detector pool would multiply the thread ceiling by the
# number of detectors and defeat the bound.
_SHARED = BoundedInferenceRunner()


def shared_runner() -> BoundedInferenceRunner:
    return _SHARED
