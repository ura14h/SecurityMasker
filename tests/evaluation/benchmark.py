"""マスキング処理のレイテンシを測定するbenchmark。

Measures added latency of the dictionary + regex + secret + JP + format pipeline
across input sizes and registered-secret counts, checking that large inputs do not
blow up quadratically. Run directly:

    .venv/bin/python -m tests.evaluation.benchmark
"""

from __future__ import annotations

import asyncio
import statistics
import time

from securitymasker.config import Defaults, EntityConfig, SecurityMaskerConfig, build_engine
from securitymasker.models import ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore


def _engine(num_secrets: int):
    entities = [
        EntityConfig(
            id=f"e{i}", type="ORGANIZATION", values=[f"組織名{i:05d}株式会社"],
            replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
            restore_policy=RestorePolicy.LITERAL.value,
        )
        for i in range(num_secrets)
    ]
    return build_engine(SecurityMaskerConfig(defaults=Defaults(), entities=entities))


def _text(size_bytes: int) -> str:
    unit = "担当は組織名00042株式会社の田中です。連絡先は03-1234-5678。 "
    return (unit * (size_bytes // len(unit.encode()) + 1))[: size_bytes]


async def _measure(engine, text: str, runs: int = 5) -> tuple[float, float]:
    store = InMemorySessionStore()
    latencies: list[float] = []
    for i in range(runs):
        session = await store.get_or_create(f"bench-{i}")
        start = time.perf_counter()
        await engine.mask_text(session, text)
        latencies.append((time.perf_counter() - start) * 1000.0)
    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
    return p50, p95


async def run() -> None:
    print("size / secrets ->  p50 ms   p95 ms")
    for size in (10_000, 100_000, 1_000_000):
        engine = _engine(100)
        p50, p95 = await _measure(engine, _text(size))
        print(f"{size:>9}B / 100     {p50:8.1f} {p95:8.1f}")
    for n in (100, 1_000, 10_000):
        engine = _engine(n)
        p50, p95 = await _measure(engine, _text(100_000))
        print(f"100000B / {n:<6}  {p50:8.1f} {p95:8.1f}")


if __name__ == "__main__":
    asyncio.run(run())
