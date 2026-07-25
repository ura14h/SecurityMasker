"""ReDoS defences (doc/06 P1-5): pattern linting + detector timeout.

The 10MB body cap does not stop catastrophic backtracking — a few dozen characters
of input against `(a+)+$` occupies a core for minutes. `re` cannot be interrupted
mid-match, so the defences are: refuse the known blow-up shapes at config load, and
bound how long we wait for any detector before failing closed.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from securitymasker.config import load_config
from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.safety import UnsafeRegexError, check_regex_safety
from securitymasker.engine import MaskingEngine
from securitymasker.errors import ConfigError, DetectionError

DANGEROUS = [
    r"(a+)+$",
    r"(a*)*b",
    r"(\d+)+x",
    r"(?:x+)+y",
    r"(a|a)*$",
    r"(foo|foobar)+",
]
SAFE = [
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"prod-db01\.internal\.example",
    r"\d{3}-\d{4}-\d{4}",
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+",
    r"(?:postgres|mysql)://[^\s]+",
]


@pytest.mark.parametrize("pattern", DANGEROUS)
def test_catastrophic_patterns_are_refused(pattern) -> None:
    with pytest.raises(UnsafeRegexError):
        check_regex_safety(pattern, rule_id="r1")


@pytest.mark.parametrize("pattern", SAFE)
def test_ordinary_patterns_pass(pattern) -> None:
    check_regex_safety(pattern, rule_id="r1")  # no raise


def test_unsafe_pattern_fails_config_load(tmp_path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "version: 1\n"
        "patterns:\n"
        "  - id: bad\n    pattern: '(a+)+$'\n    type: HOSTNAME\n"
        "    replacement_profile: hostname\n",
        encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_unsafe_pattern_error_does_not_leak_the_pattern(tmp_path) -> None:
    secret = "Zettai-Himitsu-9876"
    p = tmp_path / "c.yaml"
    p.write_text(
        "version: 1\n"
        "patterns:\n"
        f"  - id: bad\n    pattern: '({secret}+)+'\n    type: HOSTNAME\n"
        "    replacement_profile: hostname\n",
        encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert secret not in str(exc.value)


# --- detector timeout ------------------------------------------------------------


class _SlowDetector:
    name = "slow"

    async def detect(self, context: DetectionContext) -> list:
        await asyncio.sleep(5)
        return []


@pytest.mark.asyncio
async def test_slow_detector_fails_closed_within_budget() -> None:
    engine = MaskingEngine([_SlowDetector()], detector_timeout=0.05)
    started = time.monotonic()
    with pytest.raises(DetectionError):
        await engine.detect("some text")
    # We stopped waiting quickly instead of hanging for the detector's 5s.
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_timeout_can_be_disabled() -> None:
    engine = MaskingEngine([], detector_timeout=0)
    assert await engine.detect("text") == []
