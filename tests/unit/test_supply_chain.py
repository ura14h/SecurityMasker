"""Supply-chain invariants that must not silently regress (doc/06 P2-3).

Pinning is easy to do once and easy to lose: the next person edits a Dockerfile,
drops the digest, and nothing complains. These are static checks over the repo
files so an unpinned image or a dev tool in the production image fails CI rather
than shipping.

They deliberately assert on the FILES, not on a built image: they must run
anywhere, without a Docker daemon.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
RUNTIME_LOCK = ROOT / "requirements.lock"
DEV_LOCK = ROOT / "requirements-dev.lock"

_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")


# --- image pinning ------------------------------------------------------------------


def test_every_dockerfile_base_image_is_digest_pinned() -> None:
    froms = [
        line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert froms, "no FROM lines found"
    for line in froms:
        image = line.split()[1]
        # A stage that builds on a local stage (FROM runtime AS demo) inherits the
        # pinned base and needs no digest of its own.
        if image in {"runtime", "demo", "base"}:
            continue
        assert _DIGEST.search(image), f"unpinned base image: {line.strip()!r}"


def test_every_compose_image_is_digest_pinned() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for name, service in compose.get("services", {}).items():
        image = service.get("image")
        if image is None:
            assert "build" in service, f"service {name} has neither image nor build"
            continue
        assert _DIGEST.search(image), f"service {name} uses an unpinned image: {image}"


def test_pinned_digests_keep_their_tag_for_readability() -> None:
    # tag@digest, not bare digest: the tag tells a human which line they are on.
    text = DOCKERFILE.read_text(encoding="utf-8") + COMPOSE.read_text(encoding="utf-8")
    for match in _DIGEST.finditer(text):
        prefix = text[:match.start()].rsplit(None, 1)[-1]
        assert ":" in prefix, f"digest without a tag for readability: {prefix}"


# --- production image contents --------------------------------------------------------


def test_production_stage_contains_no_test_code() -> None:
    """Test/mock code belongs to the demo stage only (doc/06 P2-3)."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime_stage, _, demo_stage = text.partition("FROM runtime AS demo")
    assert "tests/" not in runtime_stage, "production stage copies test code"
    assert "mock_upstream" not in runtime_stage
    # The demo stage is where the mock is allowed to appear.
    assert "mock_upstream" in demo_stage


def test_production_stage_runs_as_non_root() -> None:
    runtime_stage = DOCKERFILE.read_text(encoding="utf-8").split("FROM runtime AS demo")[0]
    assert "USER securitymasker" in runtime_stage


def test_image_does_not_bake_the_public_bind_acknowledgement() -> None:
    # Baking it in would let `docker run -p 4000:4000` skip the safety prompt.
    runtime_stage = DOCKERFILE.read_text(encoding="utf-8").split("FROM runtime AS demo")[0]
    assert "SECURITYMASKER_ALLOW_PUBLIC_BIND=1" not in runtime_stage


def test_healthcheck_uses_readiness_not_liveness() -> None:
    # /health is up as soon as the process starts; only /ready reflects the store.
    text = DOCKERFILE.read_text(encoding="utf-8")
    healthcheck = text.split("HEALTHCHECK", 1)[1].split("\n\n")[0]
    assert "/ready" in healthcheck and "/health'" not in healthcheck


# --- dependency locks ------------------------------------------------------------------


def _lock_packages(path: Path) -> set[str]:
    return {
        line.split("==")[0].strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.strip().startswith("#")
    }


def test_runtime_lock_is_fully_pinned() -> None:
    for line in RUNTIME_LOCK.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "==" in stripped, f"unpinned runtime requirement: {stripped!r}"


def test_runtime_lock_carries_no_dev_tooling() -> None:
    # The production image installs this lock; test tooling must not ship in it.
    forbidden = {"pytest", "pytest-asyncio", "mypy", "ruff", "hypothesis", "coverage"}
    assert not (_lock_packages(RUNTIME_LOCK) & forbidden)


def test_dev_lock_is_a_superset_of_the_runtime_lock() -> None:
    runtime, dev = _lock_packages(RUNTIME_LOCK), _lock_packages(DEV_LOCK)
    missing = runtime - dev
    # CI installs the dev lock, so anything the runtime needs must be in it too;
    # otherwise CI tests a different dependency set than production ships.
    assert not missing, f"runtime packages absent from the dev lock: {sorted(missing)}"


def test_dockerfile_installs_from_the_lock_not_the_range_spec() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "-r requirements.lock" in text
    # `pip install .` without --no-deps would re-resolve the pyproject ranges and
    # quietly defeat the lock.
    assert "--no-deps" in text


@pytest.mark.parametrize("workflow", ["ci.yml"])
def test_ci_installs_from_the_lock(workflow) -> None:
    path = ROOT / ".github" / "workflows" / workflow
    if not path.exists():
        pytest.skip(f"{workflow} not present")
    text = path.read_text(encoding="utf-8")
    assert "requirements-dev.lock" in text, "CI does not install from the lock"
